"""Bounded outbox dispatcher: publish unsent extraction events, mark sent.

Runs as a one-shot command: a Cloud Run Job invoked by Cloud Scheduler in GCP,
an explicit Compose service locally. The claim lease makes concurrent runs
safe; permanently stuck rows stay visible for repair (count_stuck_outbox_rows).
"""

import argparse
import json
import logging
import sys
import time

from knowledgeforge.config import get_settings
from knowledgeforge.db import get_connection
from knowledgeforge.extraction.store import claim_outbox_batch, mark_outbox_sent
from knowledgeforge.worker.cloud import PubSubPublisher

logger = logging.getLogger("knowledgeforge.extraction.outbox")


def dispatch_once(
    *, batch_size: int, lease_seconds: int
) -> int:
    """Claim, publish, and mark one batch. Returns the number of events sent."""
    settings = get_settings()
    with get_connection() as connection:
        rows = claim_outbox_batch(connection, batch_size=batch_size, lease_seconds=lease_seconds)
    if not rows:
        return 0
    publisher = PubSubPublisher(settings.gcp_project_id, settings.extraction_topic)
    sent = 0
    for row in rows:
        publisher.publish(json.dumps(row.payload).encode())
        with get_connection() as connection:
            mark_outbox_sent(connection, row.outbox_id)
        sent += 1
        logger.info("outbox.sent job_id=%s", row.job_id)
    return sent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dispatch extraction outbox events")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--lease-seconds", type=int, default=120)
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run continuously (emulator); the default is one bounded batch.",
    )
    parser.add_argument("--interval-seconds", type=float, default=2.0)
    args = parser.parse_args(argv)

    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    if not settings.local_extraction and settings.environment != "development":
        settings.validate_runtime()
    while True:
        try:
            sent = dispatch_once(batch_size=args.batch_size, lease_seconds=args.lease_seconds)
            if sent == 0 and not args.loop:
                return 0
        except Exception:
            logger.error("outbox dispatch failed", exc_info=True)
            if not args.loop:
                return 1
        if not args.loop:
            return 0
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    sys.exit(main())
