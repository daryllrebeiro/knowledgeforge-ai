import json
from collections.abc import Callable
from uuid import UUID

from knowledgeforge.ingestion.jobs import IngestionJob, should_process


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
    get_status: Callable[[UUID], str],
    process: Callable[[IngestionJob], None],
) -> bool:
    """Process a delivery once; already-ready documents are acknowledged as duplicates."""
    job = parse_job(payload)
    if not should_process(get_status(job.document_id)):
        return False
    process(job)
    return True
