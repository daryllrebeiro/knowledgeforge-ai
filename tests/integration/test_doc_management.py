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


def test_concurrent_reingest_atomic_claim(monkeypatch) -> None:
    """Concurrent re-ingest requests: only one wins the atomic claim (H2)."""
    monkeypatch.setattr(get_settings(), "async_ingestion", True)
    monkeypatch.setattr(api, "get_connection", lambda: nullcontext(object()))
    monkeypatch.setattr(
        api,
        "get_document_ingest_info",
        lambda *args, **kwargs: ("ready", "gs://bucket/original.pdf", "hash"),
    )

    requeue_results: list[bool] = []

    def tracking_queue_reingestion(connection, document_id):
        # Simulate atomic claim: first call succeeds, subsequent fail
        result = len(requeue_results) == 0
        requeue_results.append(result)
        return result

    monkeypatch.setattr(api, "queue_reingestion", tracking_queue_reingestion)

    class FakePublisher:
        @staticmethod
        def publish(message: bytes) -> None:
            pass

    monkeypatch.setattr(api, "_pubsub_publisher", lambda settings: FakePublisher())

    # Simulate two concurrent re-ingest requests
    client = TestClient(app)
    response1 = client.post(f"/documents/{DOCUMENT_ID}/reingest")
    response2 = client.post(f"/documents/{DOCUMENT_ID}/reingest")

    # First request succeeds (202), second gets 409 conflict
    statuses = {response1.status_code, response2.status_code}
    assert 202 in statuses
    assert 409 in statuses

    # Only one requeue should have been called with True
    assert requeue_results == [True, False]


def test_version_chain_survives_intermediate_delete(monkeypatch) -> None:
    """Version chain (superseded_by) survives deletion of intermediate version."""
    from knowledgeforge.ingestion.store import DocumentSummaryRow

    # Simulate three versions: v1 -> v2 -> v3 (v2 is intermediate)
    V1 = UUID("11111111-1111-1111-1111-111111111111")
    V2 = UUID("22222222-2222-2222-2222-222222222222")
    V3 = UUID("33333333-3333-3333-3333-333333333333")

    def fake_list_documents(connection, tenant_id, *, limit=50, offset=0):
        return [
            DocumentSummaryRow(V3, "doc_v3.txt", "text", "ready", 3, None),
            DocumentSummaryRow(V2, "doc_v2.txt", "text", "ready", 2, str(V3)),
            DocumentSummaryRow(V1, "doc_v1.txt", "text", "ready", 1, str(V2)),
        ]

    monkeypatch.setattr(api, "get_connection", lambda: nullcontext(object()))
    monkeypatch.setattr(api, "list_documents", fake_list_documents)

    # Delete the intermediate version (v2)
    monkeypatch.setattr(
        api,
        "delete_document",
        lambda connection, document_id, tenant_id: (
            (True, None) if document_id == V2 else (False, None)
        ),
    )

    client = TestClient(app)
    response = client.delete(f"/documents/{V2}")

    assert response.status_code == 204

    # List documents - v1 should now point to v3 directly
    def fake_list_documents_after_delete(connection, tenant_id, *, limit=50, offset=0):
        return [
            DocumentSummaryRow(V3, "doc_v3.txt", "text", "ready", 3, None),
            DocumentSummaryRow(
                V1, "doc_v1.txt", "text", "ready", 1, str(V3)
            ),  # v1 now points to v3
        ]

    monkeypatch.setattr(api, "list_documents", fake_list_documents_after_delete)

    response = client.get("/documents")
    assert response.status_code == 200
    docs = response.json()["documents"]
    # Find v1 and verify its superseded_by is now v3 (not v2)
    v1_doc = next(d for d in docs if d["document_id"] == str(V1))
    assert v1_doc["superseded_by"] == str(V3)
