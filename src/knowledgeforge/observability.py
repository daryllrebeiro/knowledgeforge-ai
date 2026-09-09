import json
import logging
import time
from typing import Any
from uuid import UUID, uuid4

from fastapi import Request

logger = logging.getLogger("knowledgeforge.api")


class JsonLogFormatter(logging.Formatter):
    """Formats log records as JSON objects for Cloud Logging / centralized observability."""

    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        # If the message is already a serialized JSON object, enrich and return it
        if msg.startswith("{") and msg.endswith("}"):
            try:
                payload = json.loads(msg)
                payload.setdefault("timestamp", time.time())
                payload.setdefault("level", record.levelname)
                payload.setdefault("logger", record.name)
                return json.dumps(payload)
            except Exception:
                pass

        payload: dict[str, Any] = {
            "timestamp": time.time(),
            "level": record.levelname,
            "logger": record.name,
            "message": msg,
        }
        for attr in (
            "request_id",
            "tenant_id",
            "document_id",
            "job_id",
            "route",
            "status",
            "latency_ms",
            "duration_ms",
            "event",
        ):
            val = getattr(record, attr, None)
            if val is not None:
                payload[attr] = str(val) if isinstance(val, UUID) else val

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(level: str = "INFO") -> None:
    formatter = JsonLogFormatter()
    for name in (
        "knowledgeforge",
        "knowledgeforge.api",
        "knowledgeforge.worker",
        "knowledgeforge.extraction",
        "uvicorn",
        "uvicorn.access",
        "uvicorn.error",
    ):
        log = logging.getLogger(name)
        log.handlers.clear()
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        log.addHandler(handler)
        log.setLevel(level.upper())
        log.propagate = False


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
    # HSTS: enforce HTTPS for 1 year, include subdomains
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    # CSP: restrict sources to self by default if not already set by endpoint
    if "Content-Security-Policy" not in response.headers:
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "font-src 'self'; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
    if request.url.path.startswith("/auth/"):
        response.headers["Cache-Control"] = "no-store"
    return response
