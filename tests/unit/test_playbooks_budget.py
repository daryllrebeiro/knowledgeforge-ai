"""Unit tests for Phase 8 Item 6: Saved Playbooks Engine with Upfront Budget Reservation."""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from knowledgeforge.generation.generate import Citation, GeneratedAnswer
from knowledgeforge.main import app
from knowledgeforge.playbooks.runner import (
    PlaybookBudgetExceededError,
    create_playbook,
    run_playbook_question,
)

client = TestClient(app)


def test_create_and_list_playbooks():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    tenant_id = uuid4()
    playbook_id = uuid4()

    mock_cursor.fetchone.return_value = (
        playbook_id,
        tenant_id,
        "Vendor Contract Review",
        "contract",
        None,
        ["What is the governing law?", "What is the liability cap?"],
        True,
        "2026-09-09T00:00:00Z",
    )

    pb = create_playbook(
        mock_conn,
        tenant_id=tenant_id,
        name="Vendor Contract Review",
        doc_type="contract",
        questions=["What is the governing law?", "What is the liability cap?"],
    )

    assert pb.id == playbook_id
    assert pb.name == "Vendor Contract Review"
    assert len(pb.questions) == 2


def test_run_playbook_question_reserves_and_reconciles_budget(monkeypatch):
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor
    monkeypatch.setattr("knowledgeforge.retrieval.retrieve.register_vector", lambda connection: None)

    tenant_id = uuid4()
    playbook_id = uuid4()
    doc_id = uuid4()
    run_id = uuid4()

    # Mock budget
    mock_budget = MagicMock()
    mock_budget.check_and_reserve.return_value = (True, 500, 50000)

    # Mock embedding provider
    mock_embed = MagicMock()
    mock_embed.embed.return_value = [0.1] * 128

    # Mock retrieve_chunks (6 columns: c.id, c.document_id, c.page, c.section, c.chunk_text, boxes)
    mock_cursor.fetchall.return_value = [(uuid4(), doc_id, 1, "Legal", "The governing law is Delaware.", [])]

    # Mock generator
    mock_gen = MagicMock()
    mock_answer = GeneratedAnswer(
        answer="The contract is governed by Delaware law [doc 1, page 1].",
        citations=[Citation(document_index=1, page=1)],
        is_grounded=True,
    )
    monkeypatch.setattr("knowledgeforge.playbooks.runner.generate_answer", lambda *a, **kw: mock_answer)

    mock_cursor.fetchone.return_value = (
        run_id,
        tenant_id,
        playbook_id,
        doc_id,
        "What is the governing law?",
        mock_answer.answer,
        [{"document_index": 1, "page": 1}],
        "completed",
        25,
        "2026-09-09T00:00:00Z",
    )

    result = run_playbook_question(
        mock_conn,
        tenant_id=tenant_id,
        playbook_id=playbook_id,
        document_id=doc_id,
        question="What is the governing law?",
        generator=mock_gen,
        embedding_provider=mock_embed,
        token_budget=mock_budget,
        estimated_tokens=500,
    )

    assert result.id == run_id
    assert result.status == "completed"
    # Upfront reservation verified
    mock_budget.check_and_reserve.assert_called_once_with(str(tenant_id), 500)
    # Usage reconciled
    mock_budget.reconcile.assert_called_once()


def test_run_playbook_question_rejects_when_budget_exceeded():
    mock_conn = MagicMock()
    tenant_id = uuid4()
    playbook_id = uuid4()
    doc_id = uuid4()

    mock_budget = MagicMock()
    mock_budget.check_and_reserve.return_value = (False, 50000, 50000)

    with pytest.raises(PlaybookBudgetExceededError) as exc_info:
        run_playbook_question(
            mock_conn,
            tenant_id=tenant_id,
            playbook_id=playbook_id,
            document_id=doc_id,
            question="What is the payment schedule?",
            generator=MagicMock(),
            embedding_provider=MagicMock(),
            token_budget=mock_budget,
            estimated_tokens=500,
        )

    assert "Tenant daily token budget exceeded" in str(exc_info.value)
