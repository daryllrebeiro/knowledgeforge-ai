from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from knowledgeforge import api as api_module
from knowledgeforge.config import get_settings
from knowledgeforge.db import close_pool, init_pool
from knowledgeforge.limits import build_limiter
from knowledgeforge.observability import configure_logging, log_request

settings = get_settings()
api_module.limiter = build_limiter(settings.redis_url)
configure_logging(settings.log_level)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Fail closed: refuse to serve traffic on unsafe defaults (weak JWT secret,
    # missing Gemini key) outside development.
    settings.validate_runtime()
    init_pool(
        settings.database_url,
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
    )
    try:
        yield
    finally:
        close_pool()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Grounded, cited answers over tenant-isolated documents.",
    lifespan=lifespan,
)
app.middleware("http")(log_request)
app.include_router(api_module.router)
app.include_router(api_module.router, prefix="/v1")

allowed_origins = [
    origin.strip() for origin in settings.cors_allowed_origins.split(",") if origin.strip()
]
if allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    """Return a lightweight liveness response without checking dependencies."""
    return {"status": "ok"}
