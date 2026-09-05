"""LOCAL_GENERATION / LOCAL_EMBEDDINGS ask mode: full pipeline without Gemini."""

from contextlib import nullcontext
from uuid import UUID

from fastapi.testclient import TestClient

from knowledgeforge import api
from knowledgeforge.config import Settings
from knowledgeforge.conversations import MessageRow
from knowledgeforge.ingestion.chunk import TextChunk
from knowledgeforge.main import app

DOCUMENT_ID = UUID("22222222-2222-2222-2222-222222222222")
CHUNK_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
CONVERSATION_ID = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")


class FakeConnection:
    def transaction(self):
        return nullcontext(None)

    def cursor(self):
        raise AssertionError("DB should not be reached; store calls are monkeypatched")


def _local_settings(monkeypatch, **overrides) -> Settings:
    settings = Settings(local_generation=True, local_embeddings=True, **overrides)
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    monkeypatch.setattr(api, "get_connection", lambda: nullcontext(FakeConnection()))

    def fake_retrieve(*args, **kwargs):
        return [(CHUNK_ID, DOCUMENT_ID, TextChunk("chunk text", 4))]

    monkeypatch.setattr(api, "retrieve_chunks", fake_retrieve)
    return settings


def test_local_ask_answers_with_citations_and_no_gemini(monkeypatch) -> None:
    logged: dict[str, object] = {}
    _local_settings(monkeypatch)

    def fail(*args, **kwargs):
        raise AssertionError("no Gemini client should be built in local mode")

    monkeypatch.setattr(api, "_gemini_client", fail)
    monkeypatch.setattr(api, "record_request_log", lambda *args, **kwargs: logged.update(kwargs))

    response = TestClient(app).post("/ask", json={"question": "What is the answer?"})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Local answer for: What is the answer? [doc 1, page 4]"
    assert body["citations"] == [{"document_id": str(DOCUMENT_ID), "page": 4}]
    # No Gemini calls means no token usage to record.
    assert logged["input_tokens"] == 0
    assert logged["output_tokens"] == 0


def test_local_ask_refuses_when_nothing_retrieved(monkeypatch) -> None:
    _local_settings(monkeypatch)
    monkeypatch.setattr(api, "retrieve_chunks", lambda *args, **kwargs: [])

    response = TestClient(app).post("/ask", json={"question": "Anything?"})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "I don't have enough information."
    assert body["citations"] == []


def test_local_ask_skips_followup_rewrite(monkeypatch) -> None:
    retrieved_questions: list[str] = []
    _local_settings(monkeypatch)

    def fake_retrieve(connection, query_embedding, **kwargs):
        retrieved_questions.append(kwargs["question"])
        return [(CHUNK_ID, DOCUMENT_ID, TextChunk("chunk text", 4))]

    monkeypatch.setattr(api, "retrieve_chunks", fake_retrieve)
    monkeypatch.setattr(
        api,
        "get_conversation_messages",
        lambda *args, **kwargs: [
            MessageRow("user", "What is the upload limit?", [], "2026-09-03T00:00:01+00:00")
        ],
    )

    def fail_rewrite(*args, **kwargs):
        raise AssertionError("rewrite must be skipped in local mode")

    monkeypatch.setattr(api, "rewrite_followup_question", fail_rewrite)

    response = TestClient(app).post(
        "/ask", json={"question": "and downloads?", "conversation_id": str(CONVERSATION_ID)}
    )

    assert response.status_code == 200
    # Retrieval uses the raw question — no rewrite call in local mode.
    assert retrieved_questions == ["and downloads?"]


def test_local_ask_stream_emits_events(monkeypatch) -> None:
    _local_settings(monkeypatch)
    monkeypatch.setattr(api, "record_request_log", lambda *args, **kwargs: None)

    response = TestClient(app).post("/ask/stream", json={"question": "What is the answer?"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert "event: token" in body
    assert "event: done" in body
    assert "event: error" not in body
    assert '"answer": "Local answer for: What is the answer? [doc 1, page 4]"' in body
    assert f'"document_id": "{DOCUMENT_ID}"' in body
