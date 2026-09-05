"""Conversation endpoints and streaming answers (F1)."""

from contextlib import nullcontext
from uuid import UUID

from fastapi.testclient import TestClient

from knowledgeforge import api
from knowledgeforge.conversations import ConversationRow, MessageRow
from knowledgeforge.generation.generate import Citation, GeneratedAnswer
from knowledgeforge.ingestion.chunk import TextChunk
from knowledgeforge.ingestion.embed import EmbeddingResult
from knowledgeforge.main import app
from knowledgeforge.reliability import CircuitBreaker

CONVERSATION_ID = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
DOCUMENT_ID = UUID("22222222-2222-2222-2222-222222222222")
CHUNK_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")


class FakeConnection:
    def transaction(self):
        return nullcontext(None)

    def cursor(self):
        raise AssertionError("DB should not be reached; store calls are monkeypatched")


def _prepare_retrieval(monkeypatch, embedded_questions: list[str]) -> None:
    monkeypatch.setattr(api, "get_connection", lambda: nullcontext(FakeConnection()))
    monkeypatch.setattr(api, "_gemini_client", lambda: object())
    # A fresh breaker per test: the module-level one accumulates failures from
    # other tests in the same process.
    monkeypatch.setattr(api, "gemini_breaker", lambda: CircuitBreaker())

    def fake_embed(client, texts, model):
        embedded_questions.extend(texts)
        return EmbeddingResult([[0.1, 0.2]], 5)

    monkeypatch.setattr(api, "embed_texts", fake_embed)
    monkeypatch.setattr(
        api, "retrieve_chunks", lambda *args, **kwargs: [(CHUNK_ID, DOCUMENT_ID, TextChunk("c", 4))]
    )


def test_create_conversation(monkeypatch) -> None:
    monkeypatch.setattr(api, "get_connection", lambda: nullcontext(FakeConnection()))
    monkeypatch.setattr(
        api,
        "create_conversation",
        lambda connection, tenant_id, title: ConversationRow(
            CONVERSATION_ID, title, "2026-09-03T00:00:00+00:00", 0
        ),
    )

    response = TestClient(app).post("/conversations", json={"title": "Quarterly review"})

    assert response.status_code == 201
    assert response.json() == {
        "conversation_id": str(CONVERSATION_ID),
        "title": "Quarterly review",
        "updated_at": "2026-09-03T00:00:00+00:00",
        "message_count": 0,
    }


def test_list_conversations(monkeypatch) -> None:
    monkeypatch.setattr(api, "get_connection", lambda: nullcontext(FakeConnection()))
    monkeypatch.setattr(
        api,
        "list_conversations",
        lambda connection, tenant_id, **kwargs: [
            ConversationRow(CONVERSATION_ID, "Quarterly review", "2026-09-03T00:00:00+00:00", 4)
        ],
    )

    response = TestClient(app).get("/conversations")

    assert response.status_code == 200
    body = response.json()
    assert body["conversations"][0]["conversation_id"] == str(CONVERSATION_ID)
    assert body["conversations"][0]["message_count"] == 4


def test_conversation_detail_returns_messages_with_citations(monkeypatch) -> None:
    monkeypatch.setattr(api, "get_connection", lambda: nullcontext(FakeConnection()))
    monkeypatch.setattr(
        api,
        "get_conversation",
        lambda connection, conversation_id, tenant_id: ConversationRow(
            CONVERSATION_ID, "Quarterly review", "2026-09-03T00:00:00+00:00", 2
        ),
    )
    monkeypatch.setattr(
        api,
        "get_conversation_messages",
        lambda connection, conversation_id, tenant_id: [
            MessageRow("user", "What is the answer?", [], "2026-09-03T00:00:01+00:00"),
            MessageRow(
                "assistant",
                "Here. [doc 1, page 4]",
                [{"document_id": str(DOCUMENT_ID), "page": 4}],
                "2026-09-03T00:00:02+00:00",
            ),
        ],
    )

    response = TestClient(app).get(f"/conversations/{CONVERSATION_ID}")

    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Quarterly review"
    assert [message["role"] for message in body["messages"]] == ["user", "assistant"]
    assert body["messages"][1]["citations"] == [{"document_id": str(DOCUMENT_ID), "page": 4}]


def test_delete_conversation_returns_404_for_other_tenant(monkeypatch) -> None:
    monkeypatch.setattr(api, "get_connection", lambda: nullcontext(FakeConnection()))
    monkeypatch.setattr(api, "delete_conversation", lambda *args, **kwargs: False)

    response = TestClient(app).delete(f"/conversations/{CONVERSATION_ID}")

    assert response.status_code == 404


def test_ask_with_unknown_conversation_returns_404(monkeypatch) -> None:
    monkeypatch.setattr(api, "get_connection", lambda: nullcontext(FakeConnection()))
    monkeypatch.setattr(api, "_gemini_client", lambda: object())
    monkeypatch.setattr(api, "get_conversation_messages", lambda *args, **kwargs: None)

    response = TestClient(app).post(
        "/ask", json={"question": "hi", "conversation_id": str(CONVERSATION_ID)}
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Conversation not found"}


def test_ask_in_conversation_rewrites_and_persists_exchange(monkeypatch) -> None:
    embedded_questions: list[str] = []
    generated_questions: list[str] = []
    persisted: dict[str, object] = {}
    _prepare_retrieval(monkeypatch, embedded_questions)
    monkeypatch.setattr(
        api,
        "get_conversation_messages",
        lambda *args, **kwargs: [
            MessageRow("user", "What is the upload limit?", [], "2026-09-03T00:00:01+00:00"),
            MessageRow("assistant", "10 MB.", [], "2026-09-03T00:00:02+00:00"),
        ],
    )

    def fake_rewrite(generator, question, history):
        return "What is the download limit?"

    monkeypatch.setattr(api, "rewrite_followup_question", fake_rewrite)
    monkeypatch.setattr(
        api,
        "generate_answer",
        lambda generator, question, chunks: (
            generated_questions.append(question),
            GeneratedAnswer("Five MB. [doc 1, page 4]", [Citation(1, 4)], 10, 20),
        )[1],
    )
    monkeypatch.setattr(api, "record_request_log", lambda *args, **kwargs: None)

    def capture_exchange(connection, conversation_id, tenant_id, **kwargs):
        persisted.update(kwargs)

    monkeypatch.setattr(api, "append_exchange", capture_exchange)

    response = TestClient(app).post(
        "/ask", json={"question": "and downloads?", "conversation_id": str(CONVERSATION_ID)}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"] == str(CONVERSATION_ID)
    # Retrieval and generation use the rewritten standalone question...
    assert embedded_questions == ["What is the download limit?"]
    assert generated_questions == ["What is the download limit?"]
    # ...while the persisted history and request log keep the user's words.
    assert persisted["question"] == "and downloads?"
    assert persisted["answer"] == "Five MB. [doc 1, page 4]"
    assert persisted["citations"] == [{"document_id": str(DOCUMENT_ID), "page": 4}]


class FakeGeminiStream:
    """Yields fixed deltas and reports token usage like the real stream."""

    input_tokens = 100
    output_tokens = 50

    def __init__(self, client: object, model: str, prompt: str) -> None:
        self._deltas = ["The answer. ", "[doc 1, page 4]"]

    def __iter__(self):
        yield from self._deltas


def test_ask_stream_emits_token_and_done_events(monkeypatch) -> None:
    embedded_questions: list[str] = []
    persisted: dict[str, object] = {}
    _prepare_retrieval(monkeypatch, embedded_questions)
    monkeypatch.setattr(api, "GeminiTextStream", FakeGeminiStream)
    monkeypatch.setattr(api, "record_request_log", lambda *args, **kwargs: None)

    def capture_exchange(connection, conversation_id, tenant_id, **kwargs):
        persisted.update(kwargs)

    monkeypatch.setattr(api, "append_exchange", capture_exchange)

    response = TestClient(app).post("/ask/stream", json={"question": "What is the answer?"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert "event: token" in body
    assert body.count("event: token") == 2
    assert "The answer. " in body
    assert '"citations": [{"document_id": "' + str(DOCUMENT_ID) + '", "page": 4}]' in body
    assert "event: error" not in body
    # The done event carries the full assembled answer.
    assert '"answer": "The answer. [doc 1, page 4]"' in body


def test_ask_stream_in_conversation_persists_the_exchange(monkeypatch) -> None:
    embedded_questions: list[str] = []
    persisted: dict[str, object] = {}
    _prepare_retrieval(monkeypatch, embedded_questions)
    monkeypatch.setattr(
        api,
        "get_conversation_messages",
        lambda *args, **kwargs: [
            MessageRow("user", "What is the upload limit?", [], "2026-09-03T00:00:01+00:00"),
            MessageRow("assistant", "10 MB.", [], "2026-09-03T00:00:02+00:00"),
        ],
    )
    monkeypatch.setattr(
        api, "rewrite_followup_question", lambda generator, question, history: "Standalone?"
    )
    monkeypatch.setattr(api, "GeminiTextStream", FakeGeminiStream)
    monkeypatch.setattr(api, "record_request_log", lambda *args, **kwargs: None)

    def capture_exchange(connection, conversation_id, tenant_id, **kwargs):
        persisted.update(kwargs)

    monkeypatch.setattr(api, "append_exchange", capture_exchange)

    response = TestClient(app).post(
        "/ask/stream", json={"question": "and downloads?", "conversation_id": str(CONVERSATION_ID)}
    )

    assert response.status_code == 200
    assert embedded_questions == ["Standalone?"]
    assert persisted["question"] == "and downloads?"
    assert str(CONVERSATION_ID) in response.text


def test_ask_stream_reports_error_when_generation_fails(monkeypatch) -> None:
    embedded_questions: list[str] = []
    _prepare_retrieval(monkeypatch, embedded_questions)

    class ExplodingStream:
        input_tokens = 0
        output_tokens = 0

        def __init__(self, client: object, model: str, prompt: str) -> None:
            pass

        def __iter__(self):
            raise RuntimeError("provider exploded")
            yield ""  # pragma: no cover

    monkeypatch.setattr(api, "GeminiTextStream", ExplodingStream)
    monkeypatch.setattr(api, "record_request_log", lambda *args, **kwargs: None)

    response = TestClient(app).post("/ask/stream", json={"question": "What is the answer?"})

    assert response.status_code == 200
    assert "event: error" in response.text
    assert "event: done" not in response.text
