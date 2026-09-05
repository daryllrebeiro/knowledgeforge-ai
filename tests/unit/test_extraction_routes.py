"""Integration-style tests for the Phase 2.5 extraction API surface.

Uses monkeypatched store functions (no database), mirroring the existing
test_api.py / test_doc_management.py pattern. PostgreSQL-backed idempotency
and cascade tests live in tests/integration/test_extraction_postgres.py.
"""

from uuid import uuid4

from fastapi.testclient import TestClient

from knowledgeforge import api
from knowledgeforge.extraction.store import (
    DocumentExtractionRow,
    ExtractionJobRow,
)
from knowledgeforge.main import app

TENANT_ID = uuid4()
DOCUMENT_ID = uuid4()

client = TestClient(app)


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


def _patch_current_user(monkeypatch):
    app.dependency_overrides[api.get_current_user] = lambda: (uuid4(), TENANT_ID)
    monkeypatch.setattr(api, "record_request_log", lambda *args, **kwargs: None)

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(api, "get_connection", lambda: FakeConnection())


def _extraction_row(**overrides) -> DocumentExtractionRow:
    values = {
        "document_id": DOCUMENT_ID,
        "schema_type": "invoice",
        "schema_version": 1,
        "model": "gemini-2.0-flash",
        "fields": {"vendor_name": "Acme", "total": 250.0},
        "field_confidence": {"vendor_name": 0.97},
        "overall_confidence": 0.97,
        "needs_review": False,
        "created_at": "2026-09-05T00:00:00+00:00",
    }
    values.update(overrides)
    return DocumentExtractionRow(**values)


def test_document_extraction_returns_latest(monkeypatch) -> None:
    _patch_current_user(monkeypatch)
    monkeypatch.setattr(
        api, "get_document_extraction", lambda connection, document_id, tenant_id: _extraction_row()
    )
    response = client.get(f"/documents/{DOCUMENT_ID}/extraction", headers=_auth_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["schema_type"] == "invoice"
    assert body["fields"]["vendor_name"] == "Acme"


def test_document_extraction_404_when_missing(monkeypatch) -> None:
    _patch_current_user(monkeypatch)
    monkeypatch.setattr(
        api, "get_document_extraction", lambda connection, document_id, tenant_id: None
    )
    response = client.get(f"/documents/{DOCUMENT_ID}/extraction", headers=_auth_headers())
    assert response.status_code == 404


def test_extractions_list_passes_filters(monkeypatch) -> None:
    _patch_current_user(monkeypatch)
    captured: dict = {}

    def fake_list_extractions(connection, tenant_id, **kwargs):
        captured.update(kwargs)
        return [_extraction_row()]

    monkeypatch.setattr(api, "list_extractions", fake_list_extractions)
    response = client.get(
        "/extractions?schema_type=invoice&vendor_name=Acme&needs_review=false",
        headers=_auth_headers(),
    )
    assert response.status_code == 200
    assert response.json()["extractions"][0]["fields"]["vendor_name"] == "Acme"
    assert captured["schema_type"] == "invoice"
    assert captured["field_filters"] == {"vendor_name": "Acme"}
    assert captured["needs_review"] is False


def test_review_queue_lists_needs_review(monkeypatch) -> None:
    _patch_current_user(monkeypatch)
    captured: dict = {}

    def fake_list_extractions(connection, tenant_id, **kwargs):
        captured.update(kwargs)
        return [_extraction_row(needs_review=True)]

    monkeypatch.setattr(api, "list_extractions", fake_list_extractions)
    response = client.get("/admin/extractions/review-queue", headers=_auth_headers())
    assert response.status_code == 200
    assert captured["needs_review"] is True
    assert response.json()["extractions"][0]["needs_review"] is True


def test_extraction_job_status_is_tenant_scoped(monkeypatch) -> None:
    _patch_current_user(monkeypatch)
    job_id = uuid4()
    row = ExtractionJobRow(
        job_id=job_id,
        document_id=DOCUMENT_ID,
        status="succeeded",
        reason="reprocess",
        schema_type="invoice",
        schema_version=1,
        model="m",
        detail=None,
        attempt_count=1,
        created_at="2026-09-05T00:00:00+00:00",
        updated_at="2026-09-05T00:01:00+00:00",
    )
    monkeypatch.setattr(api, "get_extraction_job", lambda connection, jid, tenant_id: row)
    response = client.get(f"/extraction-jobs/{job_id}", headers=_auth_headers())
    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    monkeypatch.setattr(api, "get_extraction_job", lambda connection, jid, tenant_id: None)
    response = client.get(f"/extraction-jobs/{job_id}", headers=_auth_headers())
    assert response.status_code == 404


def test_reprocess_returns_202_with_job_id(monkeypatch) -> None:
    _patch_current_user(monkeypatch)
    monkeypatch.setattr(
        api,
        "get_document_ingest_info",
        lambda *args, **kwargs: ("ready", "gs://bucket/invoice.pdf", "hash"),
    )
    job_id = uuid4()
    monkeypatch.setattr(
        api,
        "insert_extraction_job",
        lambda connection, **kwargs: job_id,
    )
    response = client.post(
        f"/documents/{DOCUMENT_ID}/extraction/reprocess", json={}, headers=_auth_headers()
    )
    assert response.status_code == 202
    assert response.json() == {
        "job_id": str(job_id),
        "document_id": str(DOCUMENT_ID),
        "status": "queued",
    }


def test_reprocess_conflict_when_active_job(monkeypatch) -> None:
    _patch_current_user(monkeypatch)
    monkeypatch.setattr(
        api,
        "get_document_ingest_info",
        lambda *args, **kwargs: ("ready", "gs://bucket/invoice.pdf", "hash"),
    )
    monkeypatch.setattr(api, "insert_extraction_job", lambda connection, **kwargs: None)
    response = client.post(
        f"/documents/{DOCUMENT_ID}/extraction/reprocess", json={}, headers=_auth_headers()
    )
    assert response.status_code == 409


def test_reprocess_not_eligible_without_stored_original(monkeypatch) -> None:
    _patch_current_user(monkeypatch)
    monkeypatch.setattr(
        api,
        "get_document_ingest_info",
        lambda *args, **kwargs: ("ready", None, "hash"),
    )
    response = client.post(
        f"/documents/{DOCUMENT_ID}/extraction/reprocess", json={}, headers=_auth_headers()
    )
    assert response.status_code == 422


def test_reprocess_requires_ready_document(monkeypatch) -> None:
    _patch_current_user(monkeypatch)
    monkeypatch.setattr(
        api,
        "get_document_ingest_info",
        lambda *args, **kwargs: ("processing", "gs://bucket/invoice.pdf", "hash"),
    )
    response = client.post(
        f"/documents/{DOCUMENT_ID}/extraction/reprocess", json={}, headers=_auth_headers()
    )
    assert response.status_code == 409


def test_image_upload_rejected_in_sync_mode(monkeypatch) -> None:
    """OCR runs in the worker; synchronous mode has no worker to OCR with."""
    _patch_current_user(monkeypatch)
    response = client.post(
        "/documents",
        files={"file": ("scan.png", b"bytes", "image/png")},
        headers=_auth_headers(),
    )
    assert response.status_code == 415
