"""Cloud Run Pub/Sub push entrypoint for the extraction worker.

Separate service from the ingestion worker on purpose: a broken or slow
extractor can never take down chunk/embed ingestion (separate blast radius,
separate subscription, separate scaling).
"""

import base64
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from google.auth.transport import requests as auth_requests  # type: ignore[import-untyped]
from google.oauth2 import id_token  # type: ignore[import-untyped]

from knowledgeforge.config import get_settings
from knowledgeforge.extraction.jobs import parse_event
from knowledgeforge.extraction.pipeline import process_extraction_job
from knowledgeforge.observability import configure_logging

logger = logging.getLogger("knowledgeforge.extraction.worker")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    settings.validate_runtime()
    configure_logging(settings.log_level)
    yield


app = FastAPI(title="KnowledgeForge extraction worker", lifespan=lifespan)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    """Liveness for container health checks (no Pub/Sub payload involved)."""
    return {"status": "ok"}


def _verify_oidc(request: Request, audience: str) -> None:
    """In-app OIDC verification layered on top of Cloud Run invoker IAM."""
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing OIDC token")
    token = authorization.removeprefix("Bearer ")
    try:
        id_token.verify_oauth2_token(token, auth_requests.Request(), audience)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid OIDC token") from exc


@app.post("/")
async def consume(request: Request) -> dict[str, str]:
    settings = get_settings()
    if settings.extraction_worker_oidc_audience:
        _verify_oidc(request, settings.extraction_worker_oidc_audience)

    envelope = await request.json()
    payload = base64.b64decode(envelope["message"]["data"])
    event = parse_event(payload)
    started = time.monotonic()
    logger.info(
        "extraction.job.start job_id=%s document_id=%s",
        event.job_id,
        event.document_id,
        extra={
            "event": "extraction.job.start",
            "job_id": str(event.job_id),
            "document_id": str(event.document_id),
            "tenant_id": str(event.tenant_id),
        },
    )
    try:
        processed = process_extraction_job(event, settings)
    except Exception as exc:
        duration_ms = (time.monotonic() - started) * 1000
        logger.error(
            "extraction.job.failure job_id=%s duration_ms=%.0f error=%s",
            event.job_id,
            duration_ms,
            exc,
            extra={
                "event": "extraction.job.failure",
                "job_id": str(event.job_id),
                "document_id": str(event.document_id),
                "tenant_id": str(event.tenant_id),
                "duration_ms": round(duration_ms, 2),
            },
            exc_info=True,
        )
        raise
    duration_ms = (time.monotonic() - started) * 1000
    logger.info(
        "extraction.job.success job_id=%s processed=%s duration_ms=%.0f",
        event.job_id,
        processed,
        duration_ms,
        extra={
            "event": "extraction.job.success",
            "job_id": str(event.job_id),
            "document_id": str(event.document_id),
            "tenant_id": str(event.tenant_id),
            "processed": processed,
            "duration_ms": round(duration_ms, 2),
        },
    )
    return {"status": "acknowledged"}
