"""Unit tests for Fix 2: Research jobs budget system integration and rate limiting."""

from contextlib import nullcontext
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from knowledgeforge.main import app
from knowledgeforge.security import auth
from knowledgeforge.security.budget import (
    estimate_research_token_cost,
    estimate_token_cost,
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_research_token_cost_scales_with_max_iterations():
    brief = "Investigate corporate liability exposure and cross-border vendor liabilities"
    cost_1 = estimate_research_token_cost(brief, max_iterations=1)
    cost_3 = estimate_research_token_cost(brief, max_iterations=3)
    cost_10 = estimate_research_token_cost(brief, max_iterations=10)

    base = estimate_token_cost(brief, max_context_chars=10000)
    assert cost_1 == 1 * base
    assert cost_3 == 3 * base
    assert cost_10 == 10 * base
    assert cost_10 > cost_3 > cost_1


def test_research_jobs_reproduces_poc2_429_on_budget_exhaustion(client: TestClient, monkeypatch):
    user_id = uuid4()
    tenant_id = uuid4()
    monkeypatch.setattr(auth, "get_current_user", lambda: (user_id, tenant_id, "member", False))

    mock_budget = MagicMock()
    # Simulate exhausted budget: check_and_reserve returns (allowed=False, current_usage=10000, ...)
    mock_budget.check_and_reserve.return_value = (False, 10000, 10000)

    with patch("knowledgeforge.api.get_token_budget", return_value=mock_budget), \
         patch("knowledgeforge.api.get_tenant_budget_limits", return_value=(10000, 10, "free", False)):
        response = client.post(
            "/research/jobs",
            json={"brief": "Assess critical compliance gaps", "max_iterations": 3},
        )
        assert response.status_code == 429
        assert "Daily token budget exceeded" in response.json()["detail"]


def test_research_jobs_reserves_and_reconciles_on_success(client: TestClient, monkeypatch):
    user_id = uuid4()
    tenant_id = uuid4()
    monkeypatch.setattr(auth, "get_current_user", lambda: (user_id, tenant_id, "member", False))

    mock_budget = MagicMock()
    mock_budget.check_and_reserve.return_value = (True, 1500, 10000)
    mock_budget.reconcile.return_value = True

    mock_planner = MagicMock()
    mock_dossier = MagicMock(
        job_id=str(uuid4()),
        tenant_id=str(tenant_id),
        brief="Test brief",
        status="completed",
        plan=[],
        steps=[],
        executive_summary="Summary of findings",
        key_findings=["Finding A"],
        sources=["Doc 1"],
        completeness_score=0.9,
    )
    mock_planner.run_research.return_value = mock_dossier

    with patch("knowledgeforge.api.get_token_budget", return_value=mock_budget), \
         patch("knowledgeforge.api.get_tenant_budget_limits", return_value=(10000, 10, "free", False)), \
         patch("knowledgeforge.api.DeepResearchPlanner", return_value=mock_planner), \
         patch("knowledgeforge.api.get_connection", lambda: nullcontext(MagicMock())):
        response = client.post(
            "/research/jobs",
            json={"brief": "Test brief", "max_iterations": 2},
        )
        assert response.status_code == 200
        assert mock_budget.check_and_reserve.called
        assert mock_budget.reconcile.called


def test_research_jobs_releases_reservation_on_failure(client: TestClient, monkeypatch):
    user_id = uuid4()
    tenant_id = uuid4()
    monkeypatch.setattr(auth, "get_current_user", lambda: (user_id, tenant_id, "member", False))

    mock_budget = MagicMock()
    mock_budget.check_and_reserve.return_value = (True, 1500, 10000)

    mock_planner = MagicMock()
    mock_planner.run_research.side_effect = RuntimeError("Database connection lost")

    with patch("knowledgeforge.api.get_token_budget", return_value=mock_budget), \
         patch("knowledgeforge.api.get_tenant_budget_limits", return_value=(10000, 10, "free", False)), \
         patch("knowledgeforge.api.DeepResearchPlanner", return_value=mock_planner), \
         patch("knowledgeforge.api.get_connection", lambda: nullcontext(MagicMock())):
        with pytest.raises(RuntimeError, match="Database connection lost"):
            client.post(
                "/research/jobs",
                json={"brief": "Failing job", "max_iterations": 2},
            )
        assert mock_budget.release_reservation.called
