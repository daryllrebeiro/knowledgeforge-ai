"""Local/emulator Pub/Sub pull worker."""

import os
from typing import Any
from uuid import UUID

from google.auth.credentials import AnonymousCredentials
from google.cloud import pubsub_v1  # type: ignore[import-untyped]

from knowledgeforge.config import get_settings
from knowledgeforge.ingestion.store import get_document_status, update_document_status
from knowledgeforge.worker.pipeline import process_ingestion_job
from knowledgeforge.worker.processor import handle_delivery, parse_job


def main() -> None:
    settings = get_settings()
    credentials = (
        AnonymousCredentials()  # type: ignore[no-untyped-call]
        if os.getenv("PUBSUB_EMULATOR_HOST")
        else None
    )
    subscriber = pubsub_v1.SubscriberClient(credentials=credentials)
    subscription_path = subscriber.subscription_path(
        settings.gcp_project_id, settings.pubsub_subscription
    )

    def callback(message: Any) -> None:
        payload = message.data
        try:
            job = parse_job(payload)

            def get_status(document_id: UUID) -> str:
                import psycopg

                with psycopg.connect(settings.database_url) as connection:
                    return get_document_status(connection, document_id, job.tenant_id) or "failed"

            handle_delivery(
                payload,
                get_status=get_status,
                process=lambda current_job: process_ingestion_job(current_job, settings),
            )
            message.ack()
        except Exception:
            if "job" in locals():
                import psycopg

                with psycopg.connect(settings.database_url) as connection:
                    update_document_status(connection, job.document_id, "failed")
            message.nack()

    streaming = subscriber.subscribe(subscription_path, callback=callback)
    try:
        streaming.result()
    finally:
        streaming.cancel()


if __name__ == "__main__":
    main()
