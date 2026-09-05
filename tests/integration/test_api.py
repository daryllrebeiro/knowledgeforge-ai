from contextlib import nullcontext
from time import monotonic
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from knowledgeforge import api
from knowledgeforge.generation.generate import Citation, GeneratedAnswer
from knowledgeforge.ingestion.chunk import TextChunk
from knowledgeforge.ingestion.embed import EmbeddingResult
from knowledgeforge.ingestion.store import DocumentDetailRow, DocumentSummaryRow
from knowledgeforge.main import app

ROW_UUID = str(uuid4())


class FakeGeminiClient:
    pass


class FakeCursor:
    """Scripted cursor: ``results`` are returned by fetchone() in order."""

    def __init__(self, results: list[tuple] | None = None, exception: Exception | None = None):
        self.results = list(results or [])
        self.exception = exception

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, sql: str, params: object = None) -> None:
        if self.exception is not None and "INSERT INTO users" in sql:
            raise self.exception

    def fetchone(self) -> tuple | None:
        return self.results.pop(0) if self.results else (ROW_UUID,)


class FakeConnection:
    def __init__(self, results: list[tuple] | None = None, exception: Exception | None = None):
        self.results = results
        self.exception = exception

    def transaction(self):
        return nullcontext(None)

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.results, self.exception)


@pytest.fixture
def fake_db(monkeypatch):
    """Replace pooled/direct DB connections with a no-op connection."""
    monkeypatch.setattr(api, "get_connection", lambda: nullcontext(FakeConnection()))


def test_upload_document_contract(monkeypatch, fake_db) -> None:
    document_id = UUID("11111111-1111-1111-1111-111111111111")
    monkeypatch.setattr(api, "_gemini_client", lambda: FakeGeminiClient())
    monkeypatch.setattr(api, "extract_pdf", lambda _: [(1, "known content")])
    monkeypatch.setattr(
        api, "embed_texts_cached", lambda *args, **kwargs: EmbeddingResult([[0.1, 0.2]], 10)
    )
    monkeypatch.setattr(api, "find_document_by_hash", lambda *args, **kwargs: None)
    monkeypatch.setattr(api, "find_latest_document_by_filename", lambda *args, **kwargs: None)
    monkeypatch.setattr(api, "store_document", lambda *args, **kwargs: document_id)

    response = TestClient(app).post(
        "/documents",
        files={"file": ("guide.pdf", b"pdf bytes", "application/pdf")},
    )

    assert response.status_code == 202
    assert response.json() == {"document_id": str(document_id), "status": "ready"}


def test_upload_rejects_oversized_content(monkeypatch) -> None:
    from knowledgeforge.config import get_settings

    monkeypatch.setattr(get_settings(), "max_upload_bytes", 3)
    response = TestClient(app).post(
        "/documents",
        files={"file": ("large.md", b"too large", "text/markdown")},
    )
    assert response.status_code == 413


def test_register_returns_409_on_duplicate_email(monkeypatch) -> None:
    monkeypatch.setattr(
        api,
        "get_connection",
        lambda: nullcontext(FakeConnection(exception=psycopg.errors.UniqueViolation())),
    )

    response = TestClient(app).post(
        "/auth/register",
        json={"email": "dup@example.com", "password": "password123", "tenant_name": "Dup"},
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "Email already registered"}


def test_register_returns_503_on_database_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        api,
        "get_connection",
        lambda: nullcontext(FakeConnection(exception=RuntimeError("db down"))),
    )

    response = TestClient(app).post(
        "/auth/register",
        json={"email": "x@example.com", "password": "password123", "tenant_name": "X"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Registration temporarily unavailable"}


def test_ask_contract_returns_citation_with_token_telemetry(monkeypatch, fake_db) -> None:
    document_id = UUID("22222222-2222-2222-2222-222222222222")
    chunk_id = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
    chunk = TextChunk("The answer is here.", page=4)
    captured: dict[str, object] = {}
    monkeypatch.setattr(api, "_gemini_client", lambda: FakeGeminiClient())
    monkeypatch.setattr(
        api, "embed_texts", lambda *args, **kwargs: EmbeddingResult([[0.1, 0.2]], 5)
    )
    monkeypatch.setattr(
        api, "retrieve_chunks", lambda *args, **kwargs: [(chunk_id, document_id, chunk)]
    )
    monkeypatch.setattr(
        api,
        "generate_answer",
        lambda *args, **kwargs: GeneratedAnswer(
            "The answer is here. [doc 1, page 4]",
            [Citation(document_index=1, page=4)],
            input_tokens=10,
            output_tokens=20,
        ),
    )

    def capture_log(*args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(api, "record_request_log", capture_log)

    response = TestClient(app).post("/ask", json={"question": "What is the answer?"})

    assert response.status_code == 200
    assert response.json() == {
        "answer": "The answer is here. [doc 1, page 4]",
        "citations": [{"document_id": str(document_id), "page": 4}],
    }
    # Token telemetry: query embedding + generation tokens, chunk IDs not document IDs.
    assert captured["input_tokens"] == 15
    assert captured["output_tokens"] == 20
    assert captured["retrieved_chunk_ids"] == [chunk_id]
    assert captured["cost_estimate"] == 0.0


def test_ask_citations_attribute_to_the_cited_document_only(monkeypatch, fake_db) -> None:
    """Regression: two documents that both contain page 4 must not cross-credit."""
    document_a = UUID("33333333-3333-3333-3333-333333333333")
    document_b = UUID("44444444-4444-4444-4444-444444444444")
    retrieved = [
        (uuid4(), document_a, TextChunk("Answer from document A.", page=4)),
        (uuid4(), document_b, TextChunk("Unrelated page 4 content.", page=4)),
    ]
    monkeypatch.setattr(api, "_gemini_client", lambda: FakeGeminiClient())
    monkeypatch.setattr(
        api, "embed_texts", lambda *args, **kwargs: EmbeddingResult([[0.1, 0.2]], 1)
    )
    monkeypatch.setattr(api, "retrieve_chunks", lambda *args, **kwargs: retrieved)
    monkeypatch.setattr(
        api,
        "generate_answer",
        lambda *args, **kwargs: GeneratedAnswer("From A. [doc 1, page 4]", [Citation(1, 4)]),
    )

    response = TestClient(app).post("/ask", json={"question": "Who has the answer?"})

    assert response.status_code == 200
    assert response.json()["citations"] == [{"document_id": str(document_a), "page": 4}]


def test_ask_contract_preserves_grounded_refusal(monkeypatch, fake_db) -> None:
    monkeypatch.setattr(api, "_gemini_client", lambda: FakeGeminiClient())
    monkeypatch.setattr(
        api, "embed_texts", lambda *args, **kwargs: EmbeddingResult([[0.1, 0.2]], 1)
    )
    monkeypatch.setattr(api, "retrieve_chunks", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        api,
        "generate_answer",
        lambda *args, **kwargs: GeneratedAnswer("I don't have enough information.", []),
    )

    response = TestClient(app).post("/ask", json={"question": "Not in the corpus?"})

    assert response.status_code == 200
    assert response.json() == {"answer": "I don't have enough information.", "citations": []}


def test_ask_passes_authenticated_tenant_and_hybrid_settings(monkeypatch, fake_db) -> None:
    from knowledgeforge.config import get_settings

    captured: dict[str, object] = {}
    monkeypatch.setattr(api, "_gemini_client", lambda: FakeGeminiClient())
    monkeypatch.setattr(
        api, "embed_texts", lambda *args, **kwargs: EmbeddingResult([[0.1, 0.2]], 1)
    )

    def retrieve(*args, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(api, "retrieve_chunks", retrieve)
    monkeypatch.setattr(
        api,
        "generate_answer",
        lambda *args, **kwargs: GeneratedAnswer("I don't have enough information.", []),
    )
    monkeypatch.setattr(get_settings(), "hybrid_search_enabled", True)

    response = TestClient(app).post("/ask", json={"question": "Tenant private question?"})

    assert response.status_code == 200
    assert captured["tenant_id"] == UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    assert captured["question"] == "Tenant private question?"
    assert captured["hybrid"] is True


def test_ask_returns_503_when_gemini_circuit_is_open(monkeypatch, fake_db) -> None:
    """After repeated provider failures the API must fail fast, not hang."""
    from knowledgeforge.reliability import CircuitBreaker

    breaker = CircuitBreaker(failure_threshold=1, recovery_seconds=60)
    breaker.opened_at = monotonic()  # force the open state
    monkeypatch.setattr(api, "_gemini_client", lambda: FakeGeminiClient())
    monkeypatch.setattr(api, "gemini_breaker", lambda: breaker)

    response = TestClient(app).post("/ask", json={"question": "Anything?"})

    assert response.status_code == 503
    assert response.json() == {"detail": "Model provider temporarily unavailable"}


def test_batch_upload_charges_one_rate_limit_token_and_caps_files(
    monkeypatch, fake_db
) -> None:
    from knowledgeforge.config import get_settings

    checks: list[str] = []

    class CountingLimiter:
        def check(self, subject: object, key: str, capacity: int) -> None:
            checks.append(key)

    monkeypatch.setattr(api, "limiter", CountingLimiter())
    monkeypatch.setattr(get_settings(), "max_batch_files", 2)

    capped = TestClient(app).post(
        "/documents/batch",
        files=[
            ("files", ("a.txt", b"one", "text/plain")),
            ("files", ("b.txt", b"two", "text/plain")),
            ("files", ("c.txt", b"three", "text/plain")),
        ],
    )
    assert capped.status_code == 413

    response = TestClient(app).post(
        "/documents/batch",
        files=[
            ("files", ("a.exe", b"one", "application/octet-stream")),
            ("files", ("b.exe", b"two", "application/octet-stream")),
        ],
    )

    assert response.status_code == 200
    assert [item["status"] for item in response.json()] == ["failed", "failed"]
    # One token for the whole batch, not one per file.
    assert checks == ["documents"]


def test_documents_list_is_tenant_scoped_and_paginated(monkeypatch, fake_db) -> None:
    document_id = UUID("88888888-8888-8888-8888-888888888888")
    superseded_by = UUID("99999999-9999-9999-9999-999999999999")
    monkeypatch.setattr(
        api,
        "list_documents",
        lambda *args, **kwargs: [
            DocumentSummaryRow(
                document_id=document_id,
                title="guide.pdf",
                doc_type="pdf",
                status="ready",
                version=2,
                superseded_by=str(superseded_by),
            )
        ],
    )

    response = TestClient(app).get("/documents", params={"limit": 10, "offset": 20})

    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 10
    assert body["offset"] == 20
    assert body["documents"] == [
        {
            "document_id": str(document_id),
            "title": "guide.pdf",
            "doc_type": "pdf",
            "status": "ready",
            "version": 2,
            "superseded_by": str(superseded_by),
        }
    ]


def test_document_detail_includes_status_and_chunk_count(monkeypatch, fake_db) -> None:
    document_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    monkeypatch.setattr(
        api,
        "get_document_detail",
        lambda *args, **kwargs: DocumentDetailRow(
            document_id=document_id,
            title="guide.pdf",
            filename="guide.pdf",
            doc_type="pdf",
            status="ready",
            version=1,
            superseded_by=None,
            chunk_count=7,
        ),
    )

    response = TestClient(app).get(f"/documents/{document_id}")

    assert response.status_code == 200
    assert response.json() == {
        "document_id": str(document_id),
        "title": "guide.pdf",
        "filename": "guide.pdf",
        "doc_type": "pdf",
        "status": "ready",
        "version": 1,
        "superseded_by": None,
        "chunk_count": 7,
    }


def test_document_detail_returns_404_for_missing(monkeypatch, fake_db) -> None:
    monkeypatch.setattr(api, "get_document_detail", lambda *args, **kwargs: None)

    response = TestClient(app).get(f"/documents/{uuid4()}")

    assert response.status_code == 404


def test_delete_document_without_storage_uri_returns_204(monkeypatch, fake_db) -> None:
    """Regression: sync-ingested documents (NULL storage_uri) were deleted but 404'd."""
    monkeypatch.setattr(api, "delete_document", lambda *args, **kwargs: (True, None))

    response = TestClient(app).delete(f"/documents/{uuid4()}")

    assert response.status_code == 204


def test_delete_missing_document_returns_404(monkeypatch, fake_db) -> None:
    monkeypatch.setattr(api, "delete_document", lambda *args, **kwargs: (False, None))

    response = TestClient(app).delete(f"/documents/{uuid4()}")

    assert response.status_code == 404


def test_delete_document_with_storage_uri_cleans_cloud_object(monkeypatch, fake_db) -> None:
    deleted_uris: list[str] = []
    monkeypatch.setattr(
        api, "delete_document", lambda *args, **kwargs: (True, "gs://bucket/object.pdf")
    )
    monkeypatch.setattr(
        api,
        "CloudStorageClient",
        lambda *args: type("FakeStorage", (), {"delete": staticmethod(deleted_uris.append)}),
    )

    response = TestClient(app).delete(f"/documents/{uuid4()}")

    assert response.status_code == 204
    assert deleted_uris == ["gs://bucket/object.pdf"]
