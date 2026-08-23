from contextlib import nullcontext
from uuid import UUID

from fastapi.testclient import TestClient

from knowledgeforge import api
from knowledgeforge.generation.generate import Citation, GeneratedAnswer
from knowledgeforge.ingestion.chunk import TextChunk
from knowledgeforge.main import app


class FakeGeminiClient:
    pass


def test_upload_document_contract(monkeypatch) -> None:
    document_id = UUID("11111111-1111-1111-1111-111111111111")
    monkeypatch.setattr(api, "_gemini_client", lambda: FakeGeminiClient())
    monkeypatch.setattr(api, "extract_pdf", lambda _: [(1, "known content")])
    monkeypatch.setattr(api, "embed_texts", lambda *args, **kwargs: [[0.1, 0.2]])
    monkeypatch.setattr(
        api, "psycopg", type("Psycopg", (), {"connect": lambda *_: nullcontext(object())})
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


def test_ask_contract_returns_citation(monkeypatch) -> None:
    document_id = UUID("22222222-2222-2222-2222-222222222222")
    chunk = TextChunk("The answer is here.", page=4)
    monkeypatch.setattr(api, "_gemini_client", lambda: FakeGeminiClient())
    monkeypatch.setattr(api, "embed_texts", lambda *args, **kwargs: [[0.1, 0.2]])
    monkeypatch.setattr(
        api, "psycopg", type("Psycopg", (), {"connect": lambda *_: nullcontext(object())})
    )
    monkeypatch.setattr(api, "retrieve_chunks", lambda *args, **kwargs: [(document_id, chunk)])
    monkeypatch.setattr(
        api,
        "generate_answer",
        lambda *args, **kwargs: GeneratedAnswer("The answer is here. [page 4]", [Citation(4)]),
    )

    response = TestClient(app).post("/ask", json={"question": "What is the answer?"})

    assert response.status_code == 200
    assert response.json() == {
        "answer": "The answer is here. [page 4]",
        "citations": [{"document_id": str(document_id), "page": 4}],
    }


def test_ask_contract_preserves_grounded_refusal(monkeypatch) -> None:
    monkeypatch.setattr(api, "_gemini_client", lambda: FakeGeminiClient())
    monkeypatch.setattr(api, "embed_texts", lambda *args, **kwargs: [[0.1, 0.2]])
    monkeypatch.setattr(
        api, "psycopg", type("Psycopg", (), {"connect": lambda *_: nullcontext(object())})
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


def test_ask_passes_authenticated_tenant_to_retrieval(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(api, "_gemini_client", lambda: FakeGeminiClient())
    monkeypatch.setattr(api, "embed_texts", lambda *args, **kwargs: [[0.1, 0.2]])
    monkeypatch.setattr(
        api, "psycopg", type("Psycopg", (), {"connect": lambda *_: nullcontext(object())})
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

    response = TestClient(app).post("/ask", json={"question": "Tenant private question?"})

    assert response.status_code == 200
    assert captured["tenant_id"] == UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
