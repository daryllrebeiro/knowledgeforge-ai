"""Unit tests for worker and dispatcher entrypoints (Fix 5).

Covers:
- pull_entrypoint.py
- extraction_pull_entrypoint.py
- extraction_entrypoint.py
- extraction/outbox_dispatcher.py
"""

import base64
import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from knowledgeforge.extraction.jobs import ExtractionEvent
from knowledgeforge.ingestion.jobs import IngestionJob
from knowledgeforge.worker import (
    extraction_entrypoint,
    extraction_pull_entrypoint,
    pull_entrypoint,
)
from knowledgeforge.extraction import outbox_dispatcher


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_worker_settings(monkeypatch):
    from tests.unit.test_config import make_settings
    settings = make_settings(
        local_embeddings=True,
        local_generation=True,
        gcp_project_id="test-proj",
        pubsub_subscription="test-sub",
        extraction_subscription="test-ext-sub",
    )
    monkeypatch.setattr(pull_entrypoint, "get_settings", lambda: settings)
    monkeypatch.setattr(extraction_pull_entrypoint, "get_settings", lambda: settings)
    monkeypatch.setattr(extraction_entrypoint, "get_settings", lambda: settings)
    monkeypatch.setattr(outbox_dispatcher, "get_settings", lambda: settings)
    return settings


# ---------------------------------------------------------------------------
# 1. pull_entrypoint tests
# ---------------------------------------------------------------------------

def test_pull_entrypoint_callback_success(monkeypatch):
    doc_id = uuid4()
    tenant_id = uuid4()
    job_payload = {
        "document_id": str(doc_id),
        "tenant_id": str(tenant_id),
        "storage_uri": f"gs://test-bucket/{doc_id}.pdf",
        "content_hash": "hash_abc_123",
    }
    raw_bytes = json.dumps(job_payload).encode("utf-8")

    mock_msg = MagicMock()
    mock_msg.data = raw_bytes

    mock_handle_delivery = MagicMock(return_value=True)
    monkeypatch.setattr(pull_entrypoint, "handle_delivery", mock_handle_delivery)

    captured_callback = None

    class MockSubscriber:
        def subscription_path(self, project, subscription):
            return f"projects/{project}/subscriptions/{subscription}"

        def subscribe(self, path, callback):
            nonlocal captured_callback
            captured_callback = callback
            mock_stream = MagicMock()
            mock_stream.result.return_value = None
            return mock_stream

    monkeypatch.setattr(pull_entrypoint.pubsub_v1, "SubscriberClient", lambda **kwargs: MockSubscriber())

    pull_entrypoint.main()

    assert captured_callback is not None
    captured_callback(mock_msg)

    assert mock_handle_delivery.called
    assert mock_msg.ack.called
    assert not mock_msg.nack.called


def test_pull_entrypoint_callback_failure(monkeypatch):
    doc_id = uuid4()
    tenant_id = uuid4()
    job_payload = {
        "document_id": str(doc_id),
        "tenant_id": str(tenant_id),
        "storage_uri": f"gs://test-bucket/{doc_id}.pdf",
        "content_hash": "hash_abc_123",
    }
    mock_msg = MagicMock()
    mock_msg.data = json.dumps(job_payload).encode("utf-8")

    def failing_handle(*args, **kwargs):
        raise RuntimeError("Database connection lost during processing")

    monkeypatch.setattr(pull_entrypoint, "handle_delivery", failing_handle)

    mock_update_status = MagicMock()
    monkeypatch.setattr(pull_entrypoint, "update_document_status", mock_update_status)

    class MockConnContext:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(pull_entrypoint, "get_connection", lambda: MockConnContext())

    captured_callback = None

    class MockSubscriber:
        def subscription_path(self, project, subscription):
            return "sub_path"

        def subscribe(self, path, callback):
            nonlocal captured_callback
            captured_callback = callback
            mock_stream = MagicMock()
            mock_stream.result.return_value = None
            return mock_stream

    monkeypatch.setattr(pull_entrypoint.pubsub_v1, "SubscriberClient", lambda **kwargs: MockSubscriber())

    pull_entrypoint.main()
    captured_callback(mock_msg)

    assert mock_msg.nack.called
    assert not mock_msg.ack.called
    assert mock_update_status.called


def test_pull_entrypoint_claim_document(monkeypatch):
    mock_claim = MagicMock(return_value=True)
    monkeypatch.setattr(pull_entrypoint, "claim_document", mock_claim)
    monkeypatch.setattr(pull_entrypoint, "process_ingestion_job", MagicMock())

    class MockConnContext:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(pull_entrypoint, "get_connection", lambda: MockConnContext())

    doc_id = uuid4()
    tenant_id = uuid4()
    job_payload = {
        "document_id": str(doc_id),
        "tenant_id": str(tenant_id),
        "storage_uri": f"gs://test-bucket/{doc_id}.pdf",
        "content_hash": "hash_abc_123",
    }
    mock_msg = MagicMock()
    mock_msg.data = json.dumps(job_payload).encode("utf-8")

    captured_callback = None

    class MockSubscriber:
        def subscription_path(self, p, s):
            return "sub"

        def subscribe(self, path, callback):
            nonlocal captured_callback
            captured_callback = callback
            mock_stream = MagicMock()
            mock_stream.result.return_value = None
            return mock_stream

    monkeypatch.setattr(pull_entrypoint.pubsub_v1, "SubscriberClient", lambda **kwargs: MockSubscriber())
    pull_entrypoint.main()

    captured_callback(mock_msg)
    assert mock_claim.called
    assert mock_msg.ack.called


# ---------------------------------------------------------------------------
# 2. extraction_pull_entrypoint tests
# ---------------------------------------------------------------------------

def test_extraction_pull_entrypoint_success(monkeypatch):
    event_payload = {
        "job_id": str(uuid4()),
        "document_id": str(uuid4()),
        "tenant_id": str(uuid4()),
        "content_hash": "hash123",
        "schema_type": "invoice",
        "schema_version": 1,
        "model": "gemini-2.0-flash",
        "reason": "ready",
    }
    raw_bytes = json.dumps(event_payload).encode("utf-8")

    mock_msg = MagicMock()
    mock_msg.data = raw_bytes

    mock_process = MagicMock(return_value=True)
    monkeypatch.setattr(extraction_pull_entrypoint, "process_extraction_job", mock_process)

    captured_callback = None

    class MockSubscriber:
        def subscription_path(self, project, subscription):
            return "sub_path"

        def subscribe(self, path, callback):
            nonlocal captured_callback
            captured_callback = callback
            mock_stream = MagicMock()
            mock_stream.result.return_value = None
            return mock_stream

    monkeypatch.setattr(extraction_pull_entrypoint.pubsub_v1, "SubscriberClient", lambda **kwargs: MockSubscriber())

    extraction_pull_entrypoint.main()

    assert captured_callback is not None
    captured_callback(mock_msg)

    assert mock_process.called
    assert mock_msg.ack.called
    assert not mock_msg.nack.called


def test_extraction_pull_entrypoint_failure(monkeypatch):
    event_payload = {
        "job_id": str(uuid4()),
        "document_id": str(uuid4()),
        "tenant_id": str(uuid4()),
        "content_hash": "hash123",
        "schema_type": "invoice",
        "schema_version": 1,
        "model": "gemini-2.0-flash",
        "reason": "ready",
    }
    mock_msg = MagicMock()
    mock_msg.data = json.dumps(event_payload).encode("utf-8")

    def failing_process(*args, **kwargs):
        raise RuntimeError("LLM rate limit reached")

    monkeypatch.setattr(extraction_pull_entrypoint, "process_extraction_job", failing_process)

    captured_callback = None

    class MockSubscriber:
        def subscription_path(self, project, subscription):
            return "sub_path"

        def subscribe(self, path, callback):
            nonlocal captured_callback
            captured_callback = callback
            mock_stream = MagicMock()
            mock_stream.result.return_value = None
            return mock_stream

    monkeypatch.setattr(extraction_pull_entrypoint.pubsub_v1, "SubscriberClient", lambda **kwargs: MockSubscriber())

    extraction_pull_entrypoint.main()
    captured_callback(mock_msg)

    assert mock_msg.nack.called
    assert not mock_msg.ack.called


# ---------------------------------------------------------------------------
# 3. extraction_entrypoint (FastAPI push worker) tests
# ---------------------------------------------------------------------------

def test_extraction_push_health():
    client = TestClient(extraction_entrypoint.app)
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_extraction_push_consume_success(monkeypatch):
    event_payload = {
        "job_id": str(uuid4()),
        "document_id": str(uuid4()),
        "tenant_id": str(uuid4()),
        "content_hash": "hash123",
        "schema_type": "invoice",
        "schema_version": 1,
        "model": "gemini-2.0-flash",
        "reason": "ready",
    }
    raw_b64 = base64.b64encode(json.dumps(event_payload).encode("utf-8")).decode("utf-8")
    envelope = {"message": {"data": raw_b64}}

    mock_process = MagicMock(return_value=True)
    monkeypatch.setattr(extraction_entrypoint, "process_extraction_job", mock_process)

    client = TestClient(extraction_entrypoint.app)
    res = client.post("/", json=envelope)

    assert res.status_code == 200
    assert res.json() == {"status": "acknowledged"}
    assert mock_process.called


def test_extraction_push_consume_failure(monkeypatch):
    event_payload = {
        "job_id": str(uuid4()),
        "document_id": str(uuid4()),
        "tenant_id": str(uuid4()),
        "content_hash": "hash123",
        "schema_type": "invoice",
        "schema_version": 1,
        "model": "gemini-2.0-flash",
        "reason": "ready",
    }
    raw_b64 = base64.b64encode(json.dumps(event_payload).encode("utf-8")).decode("utf-8")
    envelope = {"message": {"data": raw_b64}}

    def failing_process(*args, **kwargs):
        raise ValueError("Malformed document content")

    monkeypatch.setattr(extraction_entrypoint, "process_extraction_job", failing_process)

    client = TestClient(extraction_entrypoint.app)
    with pytest.raises(ValueError, match="Malformed document content"):
        client.post("/", json=envelope)


def test_extraction_push_oidc_verification():
    mock_request = MagicMock()
    mock_request.headers = {}

    # Missing bearer token
    with pytest.raises(HTTPException) as exc_info:
        extraction_entrypoint._verify_oidc(mock_request, "expected-aud")
    assert exc_info.value.status_code == 401
    assert "Missing OIDC token" in exc_info.value.detail

    # Invalid token verification
    mock_request.headers = {"Authorization": "Bearer invalid.token.jwt"}
    with patch.object(extraction_entrypoint.id_token, "verify_oauth2_token", side_effect=ValueError("Token expired")):
        with pytest.raises(HTTPException) as exc_info2:
            extraction_entrypoint._verify_oidc(mock_request, "expected-aud")
        assert exc_info2.value.status_code == 401
        assert "Invalid OIDC token" in exc_info2.value.detail


# ---------------------------------------------------------------------------
# 4. outbox_dispatcher tests
# ---------------------------------------------------------------------------

def test_outbox_dispatcher_empty_batch(monkeypatch):
    monkeypatch.setattr(outbox_dispatcher, "claim_outbox_batch", lambda conn, **kw: [])

    class MockConnContext:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(outbox_dispatcher, "get_connection", lambda: MockConnContext())

    sent = outbox_dispatcher.dispatch_once(batch_size=10, lease_seconds=60)
    assert sent == 0


def test_outbox_dispatcher_dispatches_rows(monkeypatch):
    row1 = MagicMock()
    row1.outbox_id = uuid4()
    row1.job_id = uuid4()
    row1.payload = {"job_id": str(row1.job_id), "document_id": str(uuid4())}

    row2 = MagicMock()
    row2.outbox_id = uuid4()
    row2.job_id = uuid4()
    row2.payload = {"job_id": str(row2.job_id), "document_id": str(uuid4())}

    monkeypatch.setattr(outbox_dispatcher, "claim_outbox_batch", lambda conn, **kw: [row1, row2])

    mock_published = []
    class MockPublisher:
        def __init__(self, project, topic):
            self.topic = topic
        def publish(self, data):
            mock_published.append(data)

    monkeypatch.setattr(outbox_dispatcher, "PubSubPublisher", MockPublisher)

    mock_marked = []
    monkeypatch.setattr(outbox_dispatcher, "mark_outbox_sent", lambda conn, oid: mock_marked.append(oid))

    class MockConnContext:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(outbox_dispatcher, "get_connection", lambda: MockConnContext())

    sent = outbox_dispatcher.dispatch_once(batch_size=50, lease_seconds=120)
    assert sent == 2
    assert len(mock_published) == 2
    assert len(mock_marked) == 2
    assert mock_marked[0] == row1.outbox_id
    assert mock_marked[1] == row2.outbox_id


def test_outbox_dispatcher_main_cli(monkeypatch):
    monkeypatch.setattr(outbox_dispatcher, "dispatch_once", lambda **kw: 0)

    exit_code = outbox_dispatcher.main(["--batch-size", "25", "--lease-seconds", "30"])
    assert exit_code == 0
