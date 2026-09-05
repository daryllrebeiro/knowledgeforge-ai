"""Local/emulator Pub/Sub pull worker."""

import logging
import os
import time
from typing import Any

from google.auth.credentials import AnonymousCredentials
from google.cloud import pubsub_v1  # type: ignore[import-untyped]

from knowledgeforge.config import get_settings
from knowledgeforge.db import get_connection
from knowledgeforge.ingestion.jobs import IngestionJob
from knowledgeforge.ingestion.store import claim_document, update_document_status
from knowledgeforge.worker.pipeline import process_ingestion_job
from knowledgeforge.worker.processor import handle_delivery, parse_job

logger = logging.getLogger("knowledgeforge.worker")


def main() -> None:
    settings = get_settings()
    settings.validate_runtime()
    credentials = (
        AnonymousCredentials()  # type: ignore[no-untyped-call]
        if os.getenv("PUBSUB_EMULATOR_HOST")
        else None
    )
    subscriber = pubsub_v1.SubscriberClient(credentials=credentials)
    subscription_path = subscriber.subscription_path(
        settings.gcp_project_id, settings.pubsub_subscription
    )

    def claim(job: IngestionJob) -> bool:
        with get_connection() as connection:
            return claim_document(connection, job.document_id, job.tenant_id)

    def callback(message: Any) -> None:
        job: IngestionJob | None = None
        started = time.monotonic()
        try:
            job = parse_job(message.data)
            logger.info("job.start document_id=%s tenant_id=%s", job.document_id, job.tenant_id)
            handle_delivery(
                message.data,
                claim=claim,
                process=lambda current_job: process_ingestion_job(current_job, settings),
            )
            message.ack()
            logger.info(
                "job.success document_id=%s duration_ms=%.0f",
                job.document_id,
                (time.monotonic() - started) * 1000,
            )
        except Exception as exc:
            logger.error(
                "job.failure document_id=%s duration_ms=%.0f error=%s",
                job.document_id if job is not None else "unknown",
                (time.monotonic() - started) * 1000,
                exc,
                exc_info=True,
            )
            if job is not None:
                with get_connection() as connection:
                    update_document_status(connection, job.document_id, "failed")
            message.nack()

    streaming = subscriber.subscribe(subscription_path, callback=callback)
    try:
        streaming.result()
    finally:
        streaming.cancel()


if __name__ == "__main__":
    main()
