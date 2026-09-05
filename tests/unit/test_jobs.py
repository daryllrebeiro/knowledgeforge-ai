import json
from uuid import UUID

import pytest

from knowledgeforge.ingestion.jobs import IngestionJob
from knowledgeforge.worker.processor import handle_delivery, parse_job

DOCUMENT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TENANT_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def job_payload() -> bytes:
    return json.dumps(
        {
            "document_id": DOCUMENT_ID,
            "tenant_id": TENANT_ID,
            "storage_uri": "gs://bucket/file.pdf",
            "content_hash": "hash",
        }
    ).encode()


def test_parse_job_reads_expected_fields() -> None:
    job = parse_job(job_payload())

    assert job == IngestionJob(
        UUID(DOCUMENT_ID), UUID(TENANT_ID), "gs://bucket/file.pdf", "hash"
    )


def test_unclaimed_delivery_is_acknowledged_without_processing() -> None:
    """A duplicate redelivery loses the atomic claim and must not reprocess."""
    processed: list[IngestionJob] = []

    handled = handle_delivery(
        job_payload(), claim=lambda _: False, process=processed.append
    )

    assert handled is False
    assert processed == []


def test_claimed_delivery_is_processed_once() -> None:
    processed: list[IngestionJob] = []

    handled = handle_delivery(
        job_payload(), claim=lambda _: True, process=processed.append
    )

    assert handled is True
    assert len(processed) == 1


def test_concurrent_duplicate_loses_claim_and_does_not_double_process() -> None:
    """Only the first of two simultaneous deliveries may run the pipeline."""
    calls = 0

    def claim(job: IngestionJob) -> bool:
        nonlocal calls
        calls += 1
        return calls == 1

    processed: list[IngestionJob] = []
    first = handle_delivery(job_payload(), claim=claim, process=processed.append)
    second = handle_delivery(job_payload(), claim=claim, process=processed.append)

    assert (first, second) == (True, False)
    assert len(processed) == 1


def test_malformed_delivery_raises_for_pubsub_retry() -> None:
    with pytest.raises((KeyError, ValueError, TypeError)):
        handle_delivery(
            b'{"document_id":"not-a-job"}',
            claim=lambda _: True,
            process=lambda _: None,
        )


def test_worker_failure_propagates_for_retry() -> None:
    def crash(job: IngestionJob) -> None:
        raise RuntimeError("worker crash")

    with pytest.raises(RuntimeError, match="worker crash"):
        handle_delivery(job_payload(), claim=lambda _: True, process=crash)
