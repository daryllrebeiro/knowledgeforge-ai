"""Unit tests for Feature 9: Agentic Deep Research Planner."""

from unittest.mock import MagicMock
from uuid import uuid4

from knowledgeforge.generation.research_planner import (
    DeepResearchPlanner,
    ResearchStep,
    ResearchSubGoal,
    create_research_job,
    get_research_job,
    list_research_jobs,
    update_research_job,
)


def test_decompose_brief_heuristics() -> None:
    planner = DeepResearchPlanner()
    brief = "Who owns ACME Corp and what is the total spend and liability risk across all vendor contracts?"
    sub_goals = planner.decompose_brief(brief)

    assert len(sub_goals) >= 3
    strategies = [sg.strategy for sg in sub_goals]
    assert "graph" in strategies  # "Who owns", "vendor"
    assert "table" in strategies  # "total spend"
    assert "vector" in strategies  # "liability risk"


def test_decompose_brief_with_llm_generator() -> None:
    mock_gen = MagicMock()
    mock_gen.generate.return_value.text = (
        '[{"id": "goal_1", "description": "Map subsidiaries", "strategy": "graph"},'
        ' {"id": "goal_2", "description": "Extract spend figures", "strategy": "table"}]'
    )
    planner = DeepResearchPlanner(generator=mock_gen)
    sub_goals = planner.decompose_brief("Audit our corporate hierarchy and spend.")

    assert len(sub_goals) == 2
    assert sub_goals[0].id == "goal_1"
    assert sub_goals[0].strategy == "graph"
    assert sub_goals[1].id == "goal_2"
    assert sub_goals[1].strategy == "table"


def test_run_research_standalone_execution() -> None:
    planner = DeepResearchPlanner()
    tenant_id = uuid4()
    brief = "Evaluate customer liability caps and partner relationships in 2026."

    dossier = planner.run_research(tenant_id=tenant_id, brief=brief, max_iterations=2)

    assert dossier.job_id is not None
    assert dossier.status == "COMPLETED"
    assert len(dossier.plan) >= 2
    assert len(dossier.steps) >= 2
    assert dossier.completeness_score > 0.0
    assert "Executive Summary" in dossier.executive_summary or len(dossier.key_findings) > 0


def test_evaluate_gaps() -> None:
    planner = DeepResearchPlanner()
    plan = [
        ResearchSubGoal(
            id="g1",
            description="Find financial balance sheets",
            strategy="table",
            findings=["No direct evidence found in knowledge base"],
        )
    ]
    steps = [
        ResearchStep(
            step_number=1,
            sub_goal_id="g1",
            action="Query",
            query="Find financial sheets",
            evidence_collected=[],
        )
    ]
    gaps = planner.evaluate_gaps("Analyze financial balance sheets and timeline.", plan, steps)
    assert len(gaps) >= 1
    assert any("financial" in g.lower() for g in gaps)


def test_research_db_helpers() -> None:
    mock_conn = MagicMock()
    tenant_id = uuid4()
    job_id = uuid4()

    # Test create
    actual_id = create_research_job(
        mock_conn, tenant_id=tenant_id, query="Investigation", job_id=job_id
    )
    assert actual_id == job_id
    mock_conn.cursor.return_value.__enter__.return_value.execute.assert_called()

    # Test update
    update_research_job(
        mock_conn,
        job_id=job_id,
        status="COMPLETED",
        plan=[{"id": "g1"}],
        steps=[{"step": 1}],
        sources=["doc1.pdf"],
        final_report="Report summary",
    )
    assert mock_conn.cursor.return_value.__enter__.return_value.execute.call_count >= 2

    # Test get
    mock_cur = mock_conn.cursor.return_value.__enter__.return_value
    mock_cur.fetchone.return_value = (
        job_id,
        tenant_id,
        "Investigation",
        [{"id": "g1"}],
        [{"step": 1}],
        ["doc1.pdf"],
        "Report summary",
        "COMPLETED",
        None,
        None,
    )
    res = get_research_job(mock_conn, tenant_id=tenant_id, job_id=job_id)
    assert res is not None
    assert res["status"] == "COMPLETED"
    assert res["query"] == "Investigation"

    # Test list
    mock_cur.fetchall.return_value = [(job_id, tenant_id, "Investigation", "COMPLETED", None, None)]
    items = list_research_jobs(mock_conn, tenant_id=tenant_id)
    assert len(items) == 1
    assert items[0]["id"] == str(job_id)
