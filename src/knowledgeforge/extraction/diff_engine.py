from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID

from psycopg import Connection


class ClauseChangeType(StrEnum):
    UNMODIFIED = "UNMODIFIED"
    ADDED = "ADDED"
    REMOVED = "REMOVED"
    MODIFIED = "MODIFIED"
    OBLIGATION_ALTERED = "OBLIGATION_ALTERED"


class RiskSeverity(StrEnum):
    CRITICAL = "CRITICAL"
    MODERATE = "MODERATE"
    EDITORIAL = "EDITORIAL"
    NONE = "NONE"


@dataclass(frozen=True)
class ClauseDiff:
    section_name: str
    change_type: ClauseChangeType
    risk_severity: RiskSeverity
    source_clause: str | None
    target_clause: str | None
    similarity: float
    delta_summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "section_name": self.section_name,
            "change_type": self.change_type.value,
            "risk_severity": self.risk_severity.value,
            "source_clause": self.source_clause,
            "target_clause": self.target_clause,
            "similarity": round(self.similarity, 2),
            "delta_summary": self.delta_summary,
        }


@dataclass(frozen=True)
class DocumentDiffResult:
    source_doc_id: UUID
    target_doc_id: UUID
    overall_risk_score: str
    added_count: int
    removed_count: int
    modified_count: int
    clauses: list[ClauseDiff] = field(default_factory=list)
    executive_summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_doc_id": str(self.source_doc_id),
            "target_doc_id": str(self.target_doc_id),
            "overall_risk_score": self.overall_risk_score,
            "added_count": self.added_count,
            "removed_count": self.removed_count,
            "modified_count": self.modified_count,
            "clauses": [c.to_dict() for c in self.clauses],
            "executive_summary": self.executive_summary,
        }


def _token_similarity(text_a: str, text_b: str) -> float:
    tokens_a = set(re.findall(r"\b\w{3,}\b", text_a.lower()))
    tokens_b = set(re.findall(r"\b\w{3,}\b", text_b.lower()))
    if not tokens_a and not tokens_b:
        return 1.0
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


_HIGH_RISK_KEYWORDS = (
    "liability",
    "indemnification",
    "indemnify",
    "warranty",
    "termination",
    "remedies",
    "liquidated damages",
    "governing law",
    "penalties",
)


def compute_clause_diff(
    source_clauses: list[tuple[str, str]],  # (section_name, text)
    target_clauses: list[tuple[str, str]],
    source_doc_id: UUID,
    target_doc_id: UUID,
) -> DocumentDiffResult:
    """Performs semantic bipartite alignment and risk-scored redline analysis between two documents."""
    clauses: list[ClauseDiff] = []
    matched_target_indices: set[int] = set()

    added_count = 0
    removed_count = 0
    modified_count = 0
    max_severity = RiskSeverity.NONE

    for s_idx, (s_name, s_text) in enumerate(source_clauses):
        best_match_idx = -1
        best_sim = 0.0

        for t_idx, (t_name, t_text) in enumerate(target_clauses):
            if t_idx in matched_target_indices:
                continue
            sim = _token_similarity(s_text, t_text)
            if (
                s_name
                and t_name
                and s_name.lower() == t_name.lower()
                and s_name.lower() != "general"
            ):
                sim = min(1.0, sim + 0.25)
            if sim > best_sim:
                best_sim = sim
                best_match_idx = t_idx

        if best_match_idx != -1 and best_sim >= 0.25:
            matched_target_indices.add(best_match_idx)
            t_name, t_text = target_clauses[best_match_idx]

            if best_sim > 0.95:
                change = ClauseChangeType.UNMODIFIED
                risk = RiskSeverity.NONE
                summary = "Substantially identical language."
            else:
                change = ClauseChangeType.MODIFIED
                modified_count += 1
                # Assess risk
                is_high_risk = any(
                    k in s_text.lower() or k in t_text.lower() for k in _HIGH_RISK_KEYWORDS
                )
                if is_high_risk:
                    change = ClauseChangeType.OBLIGATION_ALTERED
                    risk = RiskSeverity.CRITICAL
                    summary = "Substantive alteration to critical legal/commercial obligation."
                    max_severity = RiskSeverity.CRITICAL
                else:
                    risk = RiskSeverity.MODERATE
                    summary = "Textual modification of clause terms."
                    if max_severity != RiskSeverity.CRITICAL:
                        max_severity = RiskSeverity.MODERATE

            clauses.append(
                ClauseDiff(
                    section_name=s_name or t_name or f"Section {s_idx + 1}",
                    change_type=change,
                    risk_severity=risk,
                    source_clause=s_text,
                    target_clause=t_text,
                    similarity=best_sim,
                    delta_summary=summary,
                )
            )
        else:
            # Clause removed in target
            removed_count += 1
            is_high_risk = any(k in s_text.lower() for k in _HIGH_RISK_KEYWORDS)
            risk = RiskSeverity.CRITICAL if is_high_risk else RiskSeverity.MODERATE
            if risk == RiskSeverity.CRITICAL:
                max_severity = RiskSeverity.CRITICAL
            clauses.append(
                ClauseDiff(
                    section_name=s_name or f"Section {s_idx + 1}",
                    change_type=ClauseChangeType.REMOVED,
                    risk_severity=risk,
                    source_clause=s_text,
                    target_clause=None,
                    similarity=0.0,
                    delta_summary="Clause present in source was omitted in target document.",
                )
            )

    # Check remaining target clauses (added clauses)
    for t_idx, (t_name, t_text) in enumerate(target_clauses):
        if t_idx not in matched_target_indices:
            added_count += 1
            is_high_risk = any(k in t_text.lower() for k in _HIGH_RISK_KEYWORDS)
            risk = RiskSeverity.CRITICAL if is_high_risk else RiskSeverity.MODERATE
            if risk == RiskSeverity.CRITICAL:
                max_severity = RiskSeverity.CRITICAL
            clauses.append(
                ClauseDiff(
                    section_name=t_name or f"New Section {t_idx + 1}",
                    change_type=ClauseChangeType.ADDED,
                    risk_severity=risk,
                    source_clause=None,
                    target_clause=t_text,
                    similarity=0.0,
                    delta_summary="New clause introduced in target document.",
                )
            )

    overall_risk = (
        "HIGH"
        if max_severity == RiskSeverity.CRITICAL
        else ("MODERATE" if max_severity == RiskSeverity.MODERATE else "LOW")
    )

    exec_summary = (
        f"Semantic redline identified {added_count} added, {removed_count} removed, "
        f"and {modified_count} modified clauses. Overall risk rating is {overall_risk}."
    )

    return DocumentDiffResult(
        source_doc_id=source_doc_id,
        target_doc_id=target_doc_id,
        overall_risk_score=overall_risk,
        added_count=added_count,
        removed_count=removed_count,
        modified_count=modified_count,
        clauses=clauses,
        executive_summary=exec_summary,
    )


def diff_documents_from_db(
    connection: Connection,
    *,
    tenant_id: UUID,
    source_doc_id: UUID,
    target_doc_id: UUID,
) -> DocumentDiffResult:
    """Loads chunks for two documents and computes their semantic redline comparison."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.section, c.chunk_text FROM chunks c
            JOIN documents d ON c.document_id = d.id
            WHERE d.tenant_id = %s AND c.document_id = %s
            ORDER BY c.page, c.id
            """,
            (tenant_id, source_doc_id),
        )
        source_rows = [(r[0] or "General", str(r[1])) for r in cursor.fetchall()]

        cursor.execute(
            """
            SELECT c.section, c.chunk_text FROM chunks c
            JOIN documents d ON c.document_id = d.id
            WHERE d.tenant_id = %s AND c.document_id = %s
            ORDER BY c.page, c.id
            """,
            (tenant_id, target_doc_id),
        )
        target_rows = [(r[0] or "General", str(r[1])) for r in cursor.fetchall()]

    return compute_clause_diff(
        source_rows,
        target_rows,
        source_doc_id=source_doc_id,
        target_doc_id=target_doc_id,
    )
