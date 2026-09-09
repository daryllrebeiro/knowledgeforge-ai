from uuid import uuid4

from knowledgeforge.api import ConflictResolveRequest, ConflictResponse
from knowledgeforge.worker.auditor import (
    ConflictCategory,
    ConflictSeverity,
    detect_statement_contradictions,
)


def test_detect_statement_contradictions_notice_days() -> None:
    doc_a = uuid4()
    doc_b = uuid4()

    text_a = "Either party may terminate this agreement with 30 days prior written notice."
    text_b = "Termination requires at least 60 calendar days written notice."

    conflicts = detect_statement_contradictions(text_a, text_b, doc_a, doc_b)
    assert len(conflicts) == 1
    assert conflicts[0].conflict_category == ConflictCategory.COMMERCIAL_TERMS_MISMATCH
    assert conflicts[0].severity == ConflictSeverity.CRITICAL
    assert "Notice period mismatch" in conflicts[0].description


def test_detect_statement_contradictions_payment_terms() -> None:
    doc_a = uuid4()
    doc_b = uuid4()

    text_a = "All invoices are payable under Net 30 terms."
    text_b = "Standard supplier payments are processed Net-60 from receipt."

    conflicts = detect_statement_contradictions(text_a, text_b, doc_a, doc_b)
    assert len(conflicts) == 1
    assert conflicts[0].conflict_category == ConflictCategory.COMMERCIAL_TERMS_MISMATCH
    assert "Payment terms conflict" in conflicts[0].description


def test_detect_statement_contradictions_approval_negation() -> None:
    doc_a = uuid4()
    doc_b = uuid4()

    text_a = "For all third-party disclosures, prior written approval is required."
    text_b = "For standard disclosures, no prior approval is required."

    conflicts = detect_statement_contradictions(text_a, text_b, doc_a, doc_b)
    assert len(conflicts) == 1
    assert conflicts[0].conflict_category == ConflictCategory.POLICY_CONTRADICTION
    assert conflicts[0].severity == ConflictSeverity.CRITICAL


def test_conflict_response_models() -> None:
    conf_id = uuid4()
    doc_a = uuid4()
    doc_b = uuid4()

    resp = ConflictResponse(
        id=conf_id,
        doc_a_id=doc_a,
        doc_b_id=doc_b,
        conflict_category="POLICY_CONTRADICTION",
        description="Policy discrepancy detected",
        severity="CRITICAL",
        status="unresolved",
        created_at="2026-09-09T00:00:00Z",
    )
    assert resp.status == "unresolved"

    resolve_req = ConflictResolveRequest(status="resolved")
    assert resolve_req.status == "resolved"
