import json
from uuid import UUID

from knowledgeforge.ingestion.jobs import IngestionJob
from knowledgeforge.worker.processor import process_job


def test_duplicate_ready_job_is_acknowledged_without_processing() -> None:
    job = IngestionJob(
        UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        "gs://bucket/file.pdf",
        "hash",
    )
    payload = json.dumps(
        {
            "document_id": str(job.document_id),
            "tenant_id": str(job.tenant_id),
            "storage_uri": job.storage_uri,
            "content_hash": job.content_hash,
        }
    ).encode()
    processed: list[IngestionJob] = []

    assert not process_job(payload, get_status=lambda _: "ready", process=processed.append)
    assert processed == []


def test_pending_job_is_processed() -> None:
    payload = json.dumps(
        {
            "document_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "tenant_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "storage_uri": "gs://bucket/file.pdf",
            "content_hash": "hash",
        }
    ).encode()
    processed: list[IngestionJob] = []

    assert process_job(payload, get_status=lambda _: "pending", process=processed.append)
    assert len(processed) == 1
