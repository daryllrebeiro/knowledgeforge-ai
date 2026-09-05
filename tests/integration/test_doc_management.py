"""Document management endpoints: chunk preview and re-ingestion (F3)."""

from contextlib import nullcontext
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from knowledgeforge import api
from knowledgeforge.config import get_settings
from knowledgeforge.ingestion.store import ChunkPreviewRow
from knowledgeforge.main import app

DOCUMENT_ID = UUID("77777777-7777-7777-7777-777777777777")


def test_chunk_preview_shows_what_was_indexed(monkeypatch) -> None:
    monkeypatch.setattr(api, "get_connection", lambda: nullcontext(object()))
    monkeypatch.setattr(
        api,
        "list_document_chunks",
        lambda *args, **kwargs: [
            ChunkPreviewRow(page=1, section=None, text="First chunk of text."),
            ChunkPreviewRow(page=2, section="Results", text="Second chunk of text."),
        ],
    )

    response = TestClient(app).get(f"/documents/{DOCUMENT_ID}/chunks", params={"limit": 2})

    assert response.status_code == 200
    assert response.json() == {
        "chunks": [
            {"page": 1, "section": None, "text": "First chunk of text."},
            {"page": 2, "section": "Results", "text": "Second chunk of text."},
        ],
        "limit": 2,
        "offset": 0,
    }


def test_chunk_preview_returns_404_for_other_tenant(monkeypatch) -> None:
    monkeypatch.setattr(api, "get_connection", lambda: nullcontext(object()))
    monkeypatch.setattr(api, "list_document_chunks", lambda *args, **kwargs: None)

    response = TestClient(app).get(f"/documents/{uuid4()}/chunks")

    assert response.status_code == 404


def test_reingest_requires_async_ingestion(monkeypatch) -> None:
    # Default settings: async_ingestion is off.
    monkeypatch.setattr(get_settings(), "async_ingestion", False)

    response = TestClient(app).post(f"/documents/{DOCUMENT_ID}/reingest")

    assert response.status_code == 409


def test_reingest_requeues_and_publishes(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "async_ingestion", True)
    monkeypatch.setattr(api, "get_connection", lambda: nullcontext(object()))
    monkeypatch.setattr(
        api,
        "get_document_ingest_info",
        lambda *args, **kwargs: ("ready", "gs://bucket/original.pdf", "hash"),
    )
    requeued: list[UUID] = []
    monkeypatch.setattr(
        api,
        "queue_reingestion",
        lambda connection, document_id: requeued.append(document_id) or True,
    )
    published: list[bytes] = []

    class FakePublisher:
        @staticmethod
        def publish(message: bytes) -> None:
            published.append(message)

    monkeypatch.setattr(api, "_pubsub_publisher", lambda settings: FakePublisher())

    response = TestClient(app).post(f"/documents/{DOCUMENT_ID}/reingest")

    assert response.status_code == 202
    assert response.json() == {"document_id": str(DOCUMENT_ID), "status": "pending"}
    assert requeued == [DOCUMENT_ID]
    assert str(DOCUMENT_ID) in published[0].decode()
    assert "gs://bucket/original.pdf" in published[0].decode()


def test_reingest_rejects_documents_without_stored_originals(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "async_ingestion", True)
    monkeypatch.setattr(api, "get_connection", lambda: nullcontext(object()))
    monkeypatch.setattr(
        api, "get_document_ingest_info", lambda *args, **kwargs: ("ready", None, "hash")
    )

    response = TestClient(app).post(f"/documents/{DOCUMENT_ID}/reingest")

    assert response.status_code == 409
    assert "no stored original" in response.json()["detail"]


def test_reingest_rejects_documents_already_in_flight(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "async_ingestion", True)
    monkeypatch.setattr(api, "get_connection", lambda: nullcontext(object()))
    monkeypatch.setattr(
        api,
        "get_document_ingest_info",
        lambda *args, **kwargs: ("pending", "gs://bucket/original.pdf", "hash"),
    )

    response = TestClient(app).post(f"/documents/{DOCUMENT_ID}/reingest")

    assert response.status_code == 409
    assert "only ready/failed" in response.json()["detail"]


def test_reingest_returns_404_for_other_tenant(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "async_ingestion", True)
    monkeypatch.setattr(api, "get_connection", lambda: nullcontext(object()))
    monkeypatch.setattr(api, "get_document_ingest_info", lambda *args, **kwargs: None)

    response = TestClient(app).post(f"/documents/{uuid4()}/reingest")

    assert response.status_code == 404
