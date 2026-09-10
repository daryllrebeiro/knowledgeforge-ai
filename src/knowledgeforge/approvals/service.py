"""Document multi-step approval workflows service (Phase 8 Item 7).

Manages approval chains, instance progression, and sign-off recording.
Backstopped by PostgreSQL triggers and unique constraints so that:
1. Double-processing of any approval step is structurally prevented.
2. Incomplete approval instances cannot be set to 'approved'.
3. Documents cannot transition to 'final' while approval steps are outstanding.
"""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import Connection

logger = logging.getLogger("knowledgeforge.approvals")


@dataclass(frozen=True)
class ApprovalChainRow:
    id: UUID
    tenant_id: UUID
    name: str
    steps: list[dict[str, Any]]
    created_at: datetime


@dataclass(frozen=True)
class ApprovalInstanceRow:
    id: UUID
    tenant_id: UUID
    document_id: UUID
    chain_id: UUID
    current_step_index: int
    total_steps: int
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ApprovalActionRow:
    id: UUID
    tenant_id: UUID
    instance_id: UUID
    step_index: int
    action: str
    actor_id: UUID
    comments: str | None
    created_at: datetime


def create_approval_chain(
    connection: Connection,
    *,
    tenant_id: UUID,
    name: str,
    steps: list[dict[str, Any]],
) -> ApprovalChainRow:
    if not steps:
        raise ValueError("Approval chain must have at least one step")
    for idx, s in enumerate(steps):
        if "role" not in s:
            raise ValueError(f"Step {idx} missing required 'role'")

    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO approval_chains (tenant_id, name, steps)
            VALUES (%s, %s, %s::jsonb)
            RETURNING id, tenant_id, name, steps, created_at
            """,
            (tenant_id, name, json.dumps(steps)),
        )
        r = cursor.fetchone()
        if r is None:
            raise RuntimeError("Failed to create approval chain")
    return ApprovalChainRow(
        id=UUID(str(r[0])),
        tenant_id=UUID(str(r[1])),
        name=str(r[2]),
        steps=list(r[3]),
        created_at=r[4],
    )


def start_document_approval(
    connection: Connection,
    *,
    tenant_id: UUID,
    document_id: UUID,
    chain_id: UUID,
) -> ApprovalInstanceRow:
    """Initiate an approval lifecycle for a document."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT steps FROM approval_chains
            WHERE id = %s AND tenant_id = %s
            """,
            (chain_id, tenant_id),
        )
        chain_row = cursor.fetchone()
        if chain_row is None:
            raise ValueError("Approval chain not found for tenant")
        steps = list(chain_row[0])
        total_steps = len(steps)

        cursor.execute(
            """
            INSERT INTO approval_instances (tenant_id, document_id, chain_id, current_step_index, total_steps, status)
            VALUES (%s, %s, %s, 0, %s, 'in_progress')
            RETURNING id, tenant_id, document_id, chain_id, current_step_index, total_steps, status, created_at, updated_at
            """,
            (tenant_id, document_id, chain_id, total_steps),
        )
        r = cursor.fetchone()
        if r is None:
            raise RuntimeError("Failed to start approval instance")

    return ApprovalInstanceRow(
        id=UUID(str(r[0])),
        tenant_id=UUID(str(r[1])),
        document_id=UUID(str(r[2])),
        chain_id=UUID(str(r[3])),
        current_step_index=int(r[4]),
        total_steps=int(r[5]),
        status=str(r[6]),
        created_at=r[7],
        updated_at=r[8],
    )


def process_approval_action(
    connection: Connection,
    *,
    tenant_id: UUID,
    instance_id: UUID,
    actor_id: UUID,
    actor_role: str,
    action: str,
    comments: str | None = None,
) -> ApprovalInstanceRow:
    """Process an approve/reject action on the active step.
    
    Uses FOR UPDATE locking and UNIQUE (instance_id, step_index) constraint
    to prevent race conditions and duplicate actions.
    """
    if action not in ("approve", "reject"):
        raise ValueError("Action must be 'approve' or 'reject'")

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT ai.id, ai.tenant_id, ai.document_id, ai.chain_id, ai.current_step_index,
                   ai.total_steps, ai.status, ac.steps
            FROM approval_instances ai
            JOIN approval_chains ac ON ac.id = ai.chain_id
            WHERE ai.id = %s AND ai.tenant_id = %s
            FOR UPDATE OF ai
            """,
            (instance_id, tenant_id),
        )
        inst_row = cursor.fetchone()
        if inst_row is None:
            raise ValueError("Approval instance not found")

        inst_id, t_id, doc_id, chain_id, step_idx, total_steps, status, chain_steps = inst_row
        if status != "in_progress":
            raise ValueError(f"Approval instance is not in progress (current status: {status})")

        step_idx = int(step_idx)
        total_steps = int(total_steps)
        chain_steps = list(chain_steps)

        # Check required role for current step
        required_role = chain_steps[step_idx].get("role", "member")
        if required_role == "owner" and actor_role != "owner":
            raise PermissionError(f"Step {step_idx} requires 'owner' role")

        # Record action; UNIQUE (instance_id, step_index) guarantees no double-processing
        cursor.execute(
            """
            INSERT INTO approval_actions (tenant_id, instance_id, step_index, action, actor_id, comments)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (tenant_id, instance_id, step_idx, action, actor_id, comments),
        )

        if action == "reject":
            cursor.execute(
                """
                UPDATE approval_instances
                SET status = 'rejected', updated_at = now()
                WHERE id = %s
                RETURNING id, tenant_id, document_id, chain_id, current_step_index, total_steps, status, created_at, updated_at
                """,
                (instance_id,),
            )
        else:
            next_step = step_idx + 1
            if next_step >= total_steps:
                # All steps approved: update instance to approved
                cursor.execute(
                    """
                    UPDATE approval_instances
                    SET current_step_index = %s, status = 'approved', updated_at = now()
                    WHERE id = %s
                    RETURNING id, tenant_id, document_id, chain_id, current_step_index, total_steps, status, created_at, updated_at
                    """,
                    (next_step, instance_id),
                )
                # Mark document as final
                cursor.execute(
                    """
                    UPDATE documents
                    SET status = 'final'
                    WHERE id = %s AND tenant_id = %s
                    """,
                    (doc_id, tenant_id),
                )
            else:
                # Advance to next step
                cursor.execute(
                    """
                    UPDATE approval_instances
                    SET current_step_index = %s, updated_at = now()
                    WHERE id = %s
                    RETURNING id, tenant_id, document_id, chain_id, current_step_index, total_steps, status, created_at, updated_at
                    """,
                    (next_step, instance_id),
                )

        updated_row = cursor.fetchone()
        if updated_row is None:
            raise RuntimeError("Failed to update approval instance")

    return ApprovalInstanceRow(
        id=UUID(str(updated_row[0])),
        tenant_id=UUID(str(updated_row[1])),
        document_id=UUID(str(updated_row[2])),
        chain_id=UUID(str(updated_row[3])),
        current_step_index=int(updated_row[4]),
        total_steps=int(updated_row[5]),
        status=str(updated_row[6]),
        created_at=updated_row[7],
        updated_at=updated_row[8],
    )


def get_document_approval_instances(
    connection: Connection,
    document_id: UUID,
    tenant_id: UUID,
) -> list[ApprovalInstanceRow]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, tenant_id, document_id, chain_id, current_step_index, total_steps, status, created_at, updated_at
            FROM approval_instances
            WHERE document_id = %s AND tenant_id = %s
            ORDER BY created_at DESC
            """,
            (document_id, tenant_id),
        )
        rows = cursor.fetchall()
    return [
        ApprovalInstanceRow(
            id=UUID(str(r[0])),
            tenant_id=UUID(str(r[1])),
            document_id=UUID(str(r[2])),
            chain_id=UUID(str(r[3])),
            current_step_index=int(r[4]),
            total_steps=int(r[5]),
            status=str(r[6]),
            created_at=r[7],
            updated_at=r[8],
        )
        for r in rows
    ]
