"""Cloud Run Pub/Sub push entrypoint."""

import base64
from uuid import UUID

import psycopg
from fastapi import FastAPI, Request

from knowledgeforge.config import get_settings
from knowledgeforge.ingestion.store import get_document_status, update_document_status
from knowledgeforge.worker.pipeline import process_ingestion_job
from knowledgeforge.worker.processor import parse_job, process_job

app = FastAPI(title="KnowledgeForge ingestion worker")


@app.post("/")
async def consume(request: Request) -> dict[str, str]:
    envelope = await request.json()
    message = envelope["message"]

    payload = base64.b64decode(message["data"])
    settings = get_settings()
    job = parse_job(payload)

    def get_status(document_id: UUID) -> str:
        with psycopg.connect(settings.database_url) as connection:
            status = get_document_status(connection, document_id, job.tenant_id)
        return status or "failed"

    try:
        process_job(
            payload,
            get_status=get_status,
            process=lambda current_job: process_ingestion_job(current_job, settings),
        )
    except Exception:
        with psycopg.connect(settings.database_url) as connection:
            update_document_status(connection, job.document_id, "failed")
        raise
    return {"status": "acknowledged"}
