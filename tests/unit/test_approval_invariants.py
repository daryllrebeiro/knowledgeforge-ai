"""Unit tests for Phase 8 Item 7: Document Approval State Machine and Database Invariants."""

from unittest.mock import MagicMock
from uuid import uuid4

import psycopg.errors
import pytest
from fastapi.testclient import TestClient

from knowledgeforge.approvals.service import (
    create_approval_chain,
    process_approval_action,
)
from knowledgeforge.main import app

client = TestClient(app)


def test_create_approval_chain():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    tenant_id = uuid4()
    chain_id = uuid4()

    mock_cursor.fetchone.return_value = (
        chain_id,
        tenant_id,
        "Two-Tier Review",
        [{"step": 0, "role": "member"}, {"step": 1, "role": "owner"}],
        "2026-09-09T00:00:00Z",
    )

    chain = create_approval_chain(
        mock_conn,
        tenant_id=tenant_id,
        name="Two-Tier Review",
        steps=[{"step": 0, "role": "member"}, {"step": 1, "role": "owner"}],
    )

    assert chain.id == chain_id
    assert chain.name == "Two-Tier Review"
    assert len(chain.steps) == 2


def test_process_approval_action_intermediate_step():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    tenant_id = uuid4()
    instance_id = uuid4()
    doc_id = uuid4()
    chain_id = uuid4()
    actor_id = uuid4()

    # Step 0 of 2 approved by member
    mock_cursor.fetchone.side_effect = [
        # 1. SELECT instance & chain
        (
            instance_id,
            tenant_id,
            doc_id,
            chain_id,
            0,  # current_step_index
            2,  # total_steps
            "in_progress",
            [{"step": 0, "role": "member"}, {"step": 1, "role": "owner"}],
        ),
        # 2. UPDATE instance RETURNING
        (
            instance_id,
            tenant_id,
            doc_id,
            chain_id,
            1,  # new current_step_index
            2,  # total_steps
            "in_progress",
            "2026-09-09T00:00:00Z",
            "2026-09-09T00:00:00Z",
        ),
    ]

    updated = process_approval_action(
        mock_conn,
        tenant_id=tenant_id,
        instance_id=instance_id,
        actor_id=actor_id,
        actor_role="member",
        action="approve",
        comments="Step 0 LGTM",
    )

    assert updated.current_step_index == 1
    assert updated.status == "in_progress"

    # Verify action recorded and row-level locking used
    select_call = mock_cursor.execute.call_args_list[0][0][0]
    assert "FOR UPDATE OF ai" in select_call

    insert_action = [
        c for c in mock_cursor.execute.call_args_list
        if "INSERT INTO approval_actions" in c[0][0]
    ]
    assert len(insert_action) == 1
    assert insert_action[0][0][1] == (tenant_id, instance_id, 0, "approve", actor_id, "Step 0 LGTM")


def test_process_approval_action_final_step_marks_document_final():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    tenant_id = uuid4()
    instance_id = uuid4()
    doc_id = uuid4()
    chain_id = uuid4()
    actor_id = uuid4()

    # Step 1 of 2 approved by owner (final step)
    mock_cursor.fetchone.side_effect = [
        (
            instance_id,
            tenant_id,
            doc_id,
            chain_id,
            1,  # current_step_index
            2,  # total_steps
            "in_progress",
            [{"step": 0, "role": "member"}, {"step": 1, "role": "owner"}],
        ),
        (
            instance_id,
            tenant_id,
            doc_id,
            chain_id,
            2,  # current_step_index
            2,  # total_steps
            "approved",
            "2026-09-09T00:00:00Z",
            "2026-09-09T00:00:00Z",
        ),
    ]

    updated = process_approval_action(
        mock_conn,
        tenant_id=tenant_id,
        instance_id=instance_id,
        actor_id=actor_id,
        actor_role="owner",
        action="approve",
        comments="Final owner sign-off",
    )

    assert updated.current_step_index == 2
    assert updated.status == "approved"

    # Verify document status updated to 'final'
    doc_update = [
        c for c in mock_cursor.execute.call_args_list
        if "UPDATE documents" in c[0][0] and "status = 'final'" in c[0][0]
    ]
    assert len(doc_update) == 1
    assert doc_update[0][0][1] == (doc_id, tenant_id)


def test_process_approval_action_reject_stops_workflow():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    tenant_id = uuid4()
    instance_id = uuid4()
    doc_id = uuid4()
    chain_id = uuid4()
    actor_id = uuid4()

    mock_cursor.fetchone.side_effect = [
        (
            instance_id,
            tenant_id,
            doc_id,
            chain_id,
            0,
            2,
            "in_progress",
            [{"step": 0, "role": "member"}, {"step": 1, "role": "owner"}],
        ),
        (
            instance_id,
            tenant_id,
            doc_id,
            chain_id,
            0,
            2,
            "rejected",
            "2026-09-09T00:00:00Z",
            "2026-09-09T00:00:00Z",
        ),
    ]

    updated = process_approval_action(
        mock_conn,
        tenant_id=tenant_id,
        instance_id=instance_id,
        actor_id=actor_id,
        actor_role="member",
        action="reject",
        comments="Missing key attachments",
    )

    assert updated.status == "rejected"
    # Ensure document was NOT updated to 'final'
    doc_update = [
        c for c in mock_cursor.execute.call_args_list
        if "UPDATE documents" in c[0][0] and "status = 'final'" in c[0][0]
    ]
    assert len(doc_update) == 0


def test_process_approval_action_enforces_role_permissions():
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    tenant_id = uuid4()
    instance_id = uuid4()
    doc_id = uuid4()
    chain_id = uuid4()
    actor_id = uuid4()

    # Step 1 requires owner, but actor is member
    mock_cursor.fetchone.return_value = (
        instance_id,
        tenant_id,
        doc_id,
        chain_id,
        1,
        2,
        "in_progress",
        [{"step": 0, "role": "member"}, {"step": 1, "role": "owner"}],
    )

    with pytest.raises(PermissionError) as exc_info:
        process_approval_action(
            mock_conn,
            tenant_id=tenant_id,
            instance_id=instance_id,
            actor_id=actor_id,
            actor_role="member",
            action="approve",
        )

    assert "requires 'owner' role" in str(exc_info.value)


def test_concurrent_approval_action_collision():
    """Simulates two concurrent approval requests on the same step index."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

    tenant_id = uuid4()
    instance_id = uuid4()
    doc_id = uuid4()
    chain_id = uuid4()
    actor_id = uuid4()

    mock_cursor.fetchone.return_value = (
        instance_id,
        tenant_id,
        doc_id,
        chain_id,
        0,
        2,
        "in_progress",
        [{"step": 0, "role": "member"}, {"step": 1, "role": "owner"}],
    )

    # Simulate database unique constraint violation on approval_actions (instance_id, step_index)
    mock_cursor.execute.side_effect = [
        None,  # SELECT FOR UPDATE
        psycopg.errors.UniqueViolation("duplicate key value violates unique constraint"),
    ]

    with pytest.raises(psycopg.errors.UniqueViolation):
        process_approval_action(
            mock_conn,
            tenant_id=tenant_id,
            instance_id=instance_id,
            actor_id=actor_id,
            actor_role="member",
            action="approve",
        )
