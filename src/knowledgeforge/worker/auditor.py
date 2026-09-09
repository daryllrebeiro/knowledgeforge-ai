from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from psycopg import Connection


class ConflictCategory(StrEnum):
    POLICY_CONTRADICTION = "POLICY_CONTRADICTION"
    DATE_SCHEDULE_CONFLICT = "DATE_SCHEDULE_CONFLICT"
    COMMERCIAL_TERMS_MISMATCH = "COMMERCIAL_TERMS_MISMATCH"
    PROCEDURAL_DISCREPANCY = "PROCEDURAL_DISCREPANCY"


class ConflictSeverity(StrEnum):
    CRITICAL = "CRITICAL"
    MODERATE = "MODERATE"
    LOW = "LOW"


@dataclass(frozen=True)
class DetectedConflict:
    doc_a_id: UUID
    doc_b_id: UUID
    conflict_category: ConflictCategory
    description: str
    severity: ConflictSeverity


@dataclass(frozen=True)
class ConflictRecord:
    id: UUID
    tenant_id: UUID
    doc_a_id: UUID
    doc_b_id: UUID
    conflict_category: str
    description: str
    severity: str
    status: str
    resolved_by: UUID | None
    resolved_at: datetime | None
    created_at: datetime


def detect_statement_contradictions(
    text_a: str,
    text_b: str,
    doc_a_id: UUID,
    doc_b_id: UUID,
) -> list[DetectedConflict]:
    """Scans two passages for mutual numerical or affirmative/negative contradictions."""
    conflicts: list[DetectedConflict] = []
    text_a_lower = text_a.lower()
    text_b_lower = text_b.lower()

    # 1. Commercial Notice Days Mismatch (e.g. 30 days notice vs 60 days notice)
    m_a = re.search(
        r"(\d+)\s*(?:calendar\s+|business\s+)?days?\s*(?:prior\s+)?(?:written\s+)?notice",
        text_a_lower,
    )
    m_b = re.search(
        r"(\d+)\s*(?:calendar\s+|business\s+)?days?\s*(?:prior\s+)?(?:written\s+)?notice",
        text_b_lower,
    )
    if m_a and m_b and m_a.group(1) != m_b.group(1):
        conflicts.append(
            DetectedConflict(
                doc_a_id=doc_a_id,
                doc_b_id=doc_b_id,
                conflict_category=ConflictCategory.COMMERCIAL_TERMS_MISMATCH,
                description=(
                    f"Notice period mismatch: Document A specifies {m_a.group(1)} days notice, "
                    f"while Document B specifies {m_b.group(1)} days notice."
                ),
                severity=ConflictSeverity.CRITICAL,
            )
        )

    # 2. Payment Terms Mismatch (e.g. Net 30 vs Net 60)
    p_a = re.search(r"\bnet[\s-]*(\d{2,3})\b", text_a_lower)
    p_b = re.search(r"\bnet[\s-]*(\d{2,3})\b", text_b_lower)
    if p_a and p_b and p_a.group(1) != p_b.group(1):
        conflicts.append(
            DetectedConflict(
                doc_a_id=doc_a_id,
                doc_b_id=doc_b_id,
                conflict_category=ConflictCategory.COMMERCIAL_TERMS_MISMATCH,
                description=(
                    f"Payment terms conflict: Document A specifies Net {p_a.group(1)}, "
                    f"while Document B specifies Net {p_b.group(1)}."
                ),
                severity=ConflictSeverity.CRITICAL,
            )
        )

    # 3. Direct Policy Negation Contradiction
    if (
        "prior written approval is required" in text_a_lower
        and "no prior approval is required" in text_b_lower
    ):
        conflicts.append(
            DetectedConflict(
                doc_a_id=doc_a_id,
                doc_b_id=doc_b_id,
                conflict_category=ConflictCategory.POLICY_CONTRADICTION,
                description=(
                    "Approval policy contradiction: Document A mandates prior written approval, "
                    "whereas Document B waives prior approval."
                ),
                severity=ConflictSeverity.CRITICAL,
            )
        )

    return conflicts


def store_conflicts(
    connection: Connection,
    *,
    tenant_id: UUID,
    conflicts: list[DetectedConflict],
) -> int:
    """Stores newly detected conflicts into knowledge_conflicts table."""
    if not conflicts:
        return 0

    with connection.cursor() as cursor:
        for c in conflicts:
            cursor.execute(
                """
                INSERT INTO knowledge_conflicts (
                    tenant_id, doc_a_id, doc_b_id, conflict_category, description, severity
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    tenant_id,
                    c.doc_a_id,
                    c.doc_b_id,
                    c.conflict_category.value,
                    c.description,
                    c.severity.value,
                ),
            )
    return len(conflicts)


def list_conflicts(
    connection: Connection,
    tenant_id: UUID,
    status: str = "unresolved",
    limit: int = 50,
) -> list[ConflictRecord]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, tenant_id, doc_a_id, doc_b_id, conflict_category,
                   description, severity, status, resolved_by, resolved_at, created_at
            FROM knowledge_conflicts
            WHERE tenant_id = %s AND status = %s
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (tenant_id, status, limit),
        )
        rows = cursor.fetchall()
        return [
            ConflictRecord(
                id=UUID(str(r[0])),
                tenant_id=UUID(str(r[1])),
                doc_a_id=UUID(str(r[2])),
                doc_b_id=UUID(str(r[3])),
                conflict_category=str(r[4]),
                description=str(r[5]),
                severity=str(r[6]),
                status=str(r[7]),
                resolved_by=UUID(str(r[8])) if r[8] else None,
                resolved_at=r[9],
                created_at=r[10],
            )
            for r in rows
        ]


def resolve_conflict(
    connection: Connection,
    conflict_id: UUID,
    tenant_id: UUID,
    user_id: UUID,
    status: str = "resolved",
) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE knowledge_conflicts
            SET status = %s, resolved_by = %s, resolved_at = now()
            WHERE id = %s AND tenant_id = %s
            RETURNING id
            """,
            (status, user_id, conflict_id, tenant_id),
        )
        return cursor.fetchone() is not None
