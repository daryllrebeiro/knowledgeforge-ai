"""Unit tests for Phase 8 Item 4: Document Drafting from Context and Budget Accounting."""

from unittest.mock import MagicMock
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from knowledgeforge import api
from knowledgeforge.generation.drafting import DraftResult, generate_draft
from knowledgeforge.generation.prompt import LabeledChunk
from knowledgeforge.ingestion.chunk import TextChunk
from knowledgeforge.main import app

client = TestClient(app)


def test_generate_draft_token_accounting_and_citations():
    mock_generator = MagicMock()
    # Mock generator returning text with inline citation
    mock_res = MagicMock()
    mock_res.text = (
        "# Strategic Brief\n\n"
        "The project timeline requires complete migration by Q3 [doc 1, page 4]. "
        "Budget constraints mandate a 15% reduction in cloud infrastructure spend [doc 2, page 1]."
    )
    mock_res.input_tokens = 450
    mock_res.output_tokens = 120
    mock_generator.generate.return_value = mock_res

    chunks = [
        LabeledChunk(
            label="doc 1",
            chunk=TextChunk(text="Complete migration scheduled for Q3.", page=4, section="Timeline"),
        ),
        LabeledChunk(
            label="doc 2",
            chunk=TextChunk(text="Infrastructure costs must reduce 15%.", page=1, section="Budget"),
        ),
    ]

    result = generate_draft(
        mock_generator,
        title="Migration Brief",
        document_type="brief",
        user_instructions="Focus on timeline and budget",
        chunks=chunks,
    )

    assert isinstance(result, DraftResult)
    assert result.title == "Migration Brief"
    assert result.document_type == "brief"
    assert result.grounded is True
    assert result.input_tokens == 450
    assert result.output_tokens == 120
    assert result.total_tokens == 570
    assert len(result.citations) >= 2


def test_post_ask_draft_budget_reconciliation_and_no_document_storage(monkeypatch):
    user_id = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    tenant_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

    app.dependency_overrides[api.get_current_user] = lambda: (
        user_id,
        tenant_id,
        "owner",
        False,
    )
    monkeypatch.setattr(api, "record_request_log", lambda *args, **kwargs: None)

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    class FakeConnection:
        def __enter__(self):
            return mock_conn

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(api, "get_connection", lambda: FakeConnection())

    # Mock retrieve_chunks returning 1 chunk
    chunk = TextChunk(text="Context clause", page=1, section="Overview")
    monkeypatch.setattr(
        api,
        "retrieve_chunks",
        lambda *args, **kwargs: [(uuid4(), uuid4(), chunk)],
    )

    # Mock Gemini client and generator
    monkeypatch.setattr(api, "_gemini_client", lambda: MagicMock())
    mock_gen = MagicMock()
    mock_gen_res = MagicMock()
    mock_gen_res.text = "Here is the memo according to [doc 1, page 1]."
    mock_gen_res.input_tokens = 300
    mock_gen_res.output_tokens = 150
    mock_gen.generate.return_value = mock_gen_res
    monkeypatch.setattr(api, "GeminiTextGenerator", lambda *args, **kwargs: mock_gen)

    # Mock Redis budget counter
    mock_budget = MagicMock()
    mock_budget.check_and_reserve.return_value = (True, 1000, 50000)
    monkeypatch.setattr(api, "get_token_budget", lambda: mock_budget)
    monkeypatch.setattr(api, "get_tenant_budget_limits", lambda t_id: (50000, 1000, 10, 10))

    res = client.post(
        "/ask/draft",
        headers={"Authorization": "Bearer test-token"},
        json={
            "title": "Vendor Memo",
            "document_type": "memo",
            "user_instructions": "Draft memo summarizing terms",
        },
    )

    assert res.status_code == 200
    data = res.json()
    assert data["title"] == "Vendor Memo"
    assert data["grounded"] is True
    assert data["total_tokens"] == 450

    # Verify budget was reserved and then reconciled
    mock_budget.check_and_reserve.assert_called_once()
    mock_budget.reconcile.assert_called_once_with(str(tenant_id), 450)

    # CRITICAL INVARIANT: Ensure no INSERT INTO documents or chunks occurred
    for call in mock_cursor.execute.call_args_list:
        sql = call[0][0].lower()
        assert "insert into documents" not in sql
        assert "insert into chunks" not in sql


def test_post_ask_draft_rejects_when_budget_exceeded(monkeypatch):
    user_id = uuid4()
    tenant_id = uuid4()

    app.dependency_overrides[api.require_scope("query:ask")] = lambda: (
        user_id,
        tenant_id,
        "member",
        False,
    )
    monkeypatch.setattr(api, "record_request_log", lambda *args, **kwargs: None)

    mock_budget = MagicMock()
    mock_budget.check_and_reserve.return_value = (False, 50000, 50000)
    monkeypatch.setattr(api, "get_token_budget", lambda: mock_budget)
    monkeypatch.setattr(api, "get_tenant_budget_limits", lambda t_id: (50000, 1000, 10, 10))

    res = client.post(
        "/ask/draft",
        headers={"Authorization": "Bearer test-token"},
        json={
            "title": "Vendor Memo",
            "document_type": "memo",
            "user_instructions": "Draft memo summarizing terms",
        },
    )

    assert res.status_code == 429
    assert "Daily token budget exceeded" in res.json()["detail"]
