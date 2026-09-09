from uuid import uuid4

from knowledgeforge.extraction.diff_engine import (
    ClauseChangeType,
    RiskSeverity,
    compute_clause_diff,
)


def test_diff_engine_identical_clauses() -> None:
    doc_a = uuid4()
    doc_b = uuid4()
    clauses_a = [
        ("Section 1", "The company provides standard customer support during business hours.")
    ]
    clauses_b = [
        ("Section 1", "The company provides standard customer support during business hours.")
    ]

    result = compute_clause_diff(clauses_a, clauses_b, doc_a, doc_b)
    assert result.overall_risk_score == "LOW"
    assert len(result.clauses) == 1
    assert result.clauses[0].change_type == ClauseChangeType.UNMODIFIED
    assert result.clauses[0].risk_severity == RiskSeverity.NONE


def test_diff_engine_detects_altered_critical_liability() -> None:
    doc_a = uuid4()
    doc_b = uuid4()
    clauses_a = [
        (
            "Indemnification",
            "Vendor shall indemnify, defend and hold harmless Customer against all third-party claims up to $5,000,000 liability.",
        )
    ]
    clauses_b = [
        (
            "Indemnification",
            "Vendor shall indemnify Customer up to a maximum liability cap of $50,000 only.",
        )
    ]

    result = compute_clause_diff(clauses_a, clauses_b, doc_a, doc_b)
    assert result.overall_risk_score == "HIGH"
    assert result.modified_count == 1
    assert result.clauses[0].change_type == ClauseChangeType.OBLIGATION_ALTERED
    assert result.clauses[0].risk_severity == RiskSeverity.CRITICAL


def test_diff_engine_detects_added_and_removed_clauses() -> None:
    doc_a = uuid4()
    doc_b = uuid4()
    clauses_a = [
        (
            "Audit Rights",
            "Customer may audit Vendor systems annually upon reasonable written notice.",
        ),
    ]
    clauses_b = [
        ("Arbitration", "All disputes shall be resolved by binding arbitration in Delaware."),
    ]

    result = compute_clause_diff(clauses_a, clauses_b, doc_a, doc_b)
    assert result.removed_count == 1
    assert result.added_count == 1

    removed = [c for c in result.clauses if c.change_type == ClauseChangeType.REMOVED]
    added = [c for c in result.clauses if c.change_type == ClauseChangeType.ADDED]
    assert len(removed) == 1
    assert len(added) == 1
