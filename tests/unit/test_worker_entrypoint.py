"""Push-worker entrypoint behavior: OIDC enforcement and delivery handling."""

import base64
import json
from contextlib import nullcontext

from fastapi.testclient import TestClient

from knowledgeforge.config import get_settings
from knowledgeforge.worker import entrypoint

DOCUMENT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
TENANT_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _delivery() -> dict:
    payload = json.dumps(
        {
            "document_id": DOCUMENT_ID,
            "tenant_id": TENANT_ID,
            "storage_uri": "gs://bucket/file.pdf",
            "content_hash": "hash",
        }
    )
    return {"message": {"data": base64.b64encode(payload.encode()).decode()}}


class FakeIdToken:
    @staticmethod
    def verify_oauth2_token(token: str, request: object, audience: str) -> dict:
        if token != "valid-token":
            raise ValueError("bad token")
        return {"audience": audience}


def test_worker_health_needs_no_authentication() -> None:
    response = TestClient(entrypoint.app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_worker_rejects_missing_oidc_token(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "worker_oidc_audience", "https://worker.example")

    response = TestClient(entrypoint.app).post("/", json=_delivery())

    assert response.status_code == 401
    assert response.json() == {"detail": "Missing OIDC token"}


def test_worker_rejects_invalid_oidc_token(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "worker_oidc_audience", "https://worker.example")
    monkeypatch.setattr(entrypoint, "id_token", FakeIdToken)

    response = TestClient(entrypoint.app).post(
        "/", json=_delivery(), headers={"Authorization": "Bearer wrong-token"}
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid OIDC token"}


def test_worker_accepts_valid_oidc_token_and_processes(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "worker_oidc_audience", "https://worker.example")
    monkeypatch.setattr(entrypoint, "id_token", FakeIdToken)
    monkeypatch.setattr(entrypoint, "get_connection", lambda: nullcontext(object()))
    monkeypatch.setattr(entrypoint, "claim_document", lambda *args, **kwargs: True)
    monkeypatch.setattr(entrypoint, "process_ingestion_job", lambda *args, **kwargs: None)

    response = TestClient(entrypoint.app).post(
        "/", json=_delivery(), headers={"Authorization": "Bearer valid-token"}
    )

    assert response.status_code == 200
    assert response.json() == {"status": "acknowledged"}


def test_worker_marks_document_failed_when_processing_raises(monkeypatch) -> None:
    statuses: list[str] = []
    monkeypatch.setattr(entrypoint, "get_connection", lambda: nullcontext(object()))
    monkeypatch.setattr(entrypoint, "claim_document", lambda *args, **kwargs: True)

    def fail(job):
        raise RuntimeError("boom")

    monkeypatch.setattr(entrypoint, "process_ingestion_job", fail)
    monkeypatch.setattr(
        entrypoint,
        "update_document_status",
        lambda connection, document_id, status: statuses.append(status),
    )

    client = TestClient(entrypoint.app, raise_server_exceptions=False)
    response = client.post("/", json=_delivery())

    assert response.status_code == 500
    assert statuses == ["failed"]
