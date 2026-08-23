from fastapi import FastAPI

from knowledgeforge.api import router
from knowledgeforge.config import get_settings
from knowledgeforge.observability import configure_logging, log_request

settings = get_settings()
configure_logging()
app = FastAPI(title=settings.app_name, version="0.1.0")
app.middleware("http")(log_request)
app.include_router(router)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    """Return a lightweight liveness response without checking dependencies."""
    return {"status": "ok"}
