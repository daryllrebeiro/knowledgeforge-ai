"""Local/emulator Pub/Sub pull entrypoint for the extraction worker."""

import logging
import os
import time
from typing import Any

from google.auth.credentials import AnonymousCredentials
from google.cloud import pubsub_v1  # type: ignore[import-untyped]

from knowledgeforge.config import get_settings
from knowledgeforge.extraction.jobs import parse_event
from knowledgeforge.extraction.pipeline import process_extraction_job

logger = logging.getLogger("knowledgeforge.extraction.worker")


def main() -> None:
    settings = get_settings()
    settings.validate_runtime()
    logging.basicConfig(level=settings.log_level.upper())
    logging.getLogger("knowledgeforge.extraction").setLevel(settings.log_level.upper())
    credentials = (
        AnonymousCredentials()  # type: ignore[no-untyped-call]
        if os.getenv("PUBSUB_EMULATOR_HOST")
        else None
    )
    subscriber = pubsub_v1.SubscriberClient(credentials=credentials)
    subscription_path = subscriber.subscription_path(
        settings.gcp_project_id, settings.extraction_subscription
    )

    def callback(message: Any) -> None:
        event = None
        started = time.monotonic()
        try:
            event = parse_event(message.data)
            logger.info(
                "extraction.job.start job_id=%s document_id=%s",
                event.job_id,
                event.document_id,
            )
            process_extraction_job(event, settings)
            message.ack()
            logger.info(
                "extraction.job.success job_id=%s duration_ms=%.0f",
                event.job_id,
                (time.monotonic() - started) * 1000,
            )
        except Exception as exc:
            logger.error(
                "extraction.job.failure job_id=%s duration_ms=%.0f error=%s",
                event.job_id if event is not None else "unknown",
                (time.monotonic() - started) * 1000,
                exc,
                exc_info=True,
            )
            message.nack()

    streaming = subscriber.subscribe(subscription_path, callback=callback)
    try:
        streaming.result()
    finally:
        streaming.cancel()


if __name__ == "__main__":
    main()
