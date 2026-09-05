"""Cloud Run Pub/Sub push entrypoint."""

import base64
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from google.auth.transport import requests as auth_requests  # type: ignore[import-untyped]
from google.oauth2 import id_token  # type: ignore[import-untyped]

from knowledgeforge.config import get_settings
from knowledgeforge.db import get_connection
from knowledgeforge.ingestion.jobs import IngestionJob
from knowledgeforge.ingestion.store import claim_document, update_document_status
from knowledgeforge.worker.pipeline import process_ingestion_job
from knowledgeforge.worker.processor import parse_job, process_job

logger = logging.getLogger("knowledgeforge.worker")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    get_settings().validate_runtime()
    yield


app = FastAPI(title="KnowledgeForge ingestion worker", lifespan=lifespan)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    """Liveness for container health checks (no Pub/Sub payload involved)."""
    return {"status": "ok"}


def _verify_oidc(request: Request, audience: str) -> None:
    """Verify the Pub/Sub push OIDC token against the expected audience.

    Cloud Run invoker IAM is the primary enforcement; this in-app check
    (enabled via WORKER_OIDC_AUDIENCE) additionally rejects any request that
    authenticated as a *different* service account.
    """
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
    if settings.worker_oidc_audience:
        _verify_oidc(request, settings.worker_oidc_audience)

    envelope = await request.json()
    message = envelope["message"]

    payload = base64.b64decode(message["data"])
    job = parse_job(payload)
    started = time.monotonic()
    logger.info("job.start document_id=%s tenant_id=%s", job.document_id, job.tenant_id)

    def claim(current_job: IngestionJob) -> bool:
        with get_connection() as connection:
            return claim_document(connection, current_job.document_id, current_job.tenant_id)

    try:
        processed = process_job(
            payload,
            claim=claim,
            process=lambda current_job: process_ingestion_job(current_job, settings),
        )
    except Exception as exc:
        duration_ms = (time.monotonic() - started) * 1000
        logger.error(
            "job.failure document_id=%s duration_ms=%.0f error=%s",
            job.document_id,
            duration_ms,
            exc,
            exc_info=True,
        )
        with get_connection() as connection:
            update_document_status(connection, job.document_id, "failed")
        raise
    duration_ms = (time.monotonic() - started) * 1000
    logger.info(
        "job.success document_id=%s processed=%s duration_ms=%.0f",
        job.document_id,
        processed,
        duration_ms,
    )
    return {"status": "acknowledged"}
