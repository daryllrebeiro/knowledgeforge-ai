import json
from collections.abc import Callable
from uuid import UUID

from knowledgeforge.ingestion.jobs import IngestionJob


def parse_job(payload: bytes) -> IngestionJob:
    data = json.loads(payload)
    return IngestionJob(
        document_id=UUID(data["document_id"]),
        tenant_id=UUID(data["tenant_id"]),
        storage_uri=data["storage_uri"],
        content_hash=data["content_hash"],
    )


def process_job(
    payload: bytes,
    *,
    claim: Callable[[IngestionJob], bool],
    process: Callable[[IngestionJob], None],
) -> bool:
    """Process a delivery once; unclaimable documents are acknowledged duplicates.

    The claim is an atomic database state transition, so concurrent redeliveries
    of the same message cannot both run the pipeline.
    """
    job = parse_job(payload)
    if not claim(job):
        return False
    process(job)
    return True


def handle_delivery(
    payload: bytes,
    *,
    claim: Callable[[IngestionJob], bool],
    process: Callable[[IngestionJob], None],
) -> bool:
    """Process a delivery and let malformed payloads reach Pub/Sub retry/DLQ."""
    return process_job(payload, claim=claim, process=process)
