import json
import logging
import time
from typing import Any
from uuid import UUID, uuid4

from fastapi import Request

logger = logging.getLogger("knowledgeforge.api")


def configure_logging() -> None:
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def request_id(request: Request) -> UUID:
    current = getattr(request.state, "request_id", None)
    if current is None:
        current = uuid4()
        request.state.request_id = current
    return current


async def log_request(request: Request, call_next: Any) -> Any:
    started = time.perf_counter()
    current_request_id = request_id(request)
    response = await call_next(request)
    latency_ms = (time.perf_counter() - started) * 1000
    logger.info(
        json.dumps(
            {
                "request_id": str(current_request_id),
                "tenant_id": str(getattr(request.state, "tenant_id", "")),
                "route": request.url.path,
                "latency_ms": round(latency_ms, 2),
                "status": response.status_code,
            }
        )
    )
    response.headers["X-Request-ID"] = str(current_request_id)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.url.path.startswith("/auth/"):
        response.headers["Cache-Control"] = "no-store"
    return response
