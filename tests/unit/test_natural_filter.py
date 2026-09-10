"""Unit tests for Natural-Language Filters Over Extracted Fields (Phase 7 Wave 1 Item A)."""

from contextlib import nullcontext
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from knowledgeforge import api
from knowledgeforge.extraction.query_parser import (
    FilterConfidence,
    parse_natural_filter,
)
from knowledgeforge.extraction.store import (
    DocumentExtractionRow,
    list_extractions_with_ranges,
)


def test_parse_amount_thresholds():
    res = parse_natural_filter("invoices over $10k")
    assert res.confidence == FilterConfidence.HIGH
    assert res.schema_type == "invoice"
    assert res.numeric_ranges.get("total") == (10000.0, None)

    res_under = parse_natural_filter("contracts under 50000")
    assert res_under.schema_type == "contract"
    assert res_under.numeric_ranges.get("total_value") == (None, 50000.0)

    res_between = parse_natural_filter("invoices between $1,000 and $5,000")
    assert res_between.numeric_ranges.get("total") == (1000.0, 5000.0)


def test_parse_date_ranges():
    res_q1 = parse_natural_filter("invoices from Q1 2026")
    assert res_q1.date_ranges.get("invoice_date") == ("2026-01-01", "2026-03-31")

    res_month = parse_natural_filter("contracts effective in March 2026")
    assert res_month.date_ranges.get("effective_date") == ("2026-03-01", "2026-03-31")

    res_iso = parse_natural_filter("invoices dated after 2026-01-15")
    assert res_iso.date_ranges.get("invoice_date")[0] == "2026-01-15"


def test_parse_field_equality():
    res_vendor = parse_natural_filter("invoices from Acme Corporation")
    assert res_vendor.field_filters.get("vendor_name") == "Acme Corporation"

    res_eur = parse_natural_filter("invoices in EUR")
    assert res_eur.field_filters.get("currency") == "EUR"

    res_contract = parse_natural_filter("contracts governed by Delaware")
    assert res_contract.field_filters.get("governing_law") == "Delaware"


def test_parse_combination():
    res = parse_natural_filter("invoices from Acme Corporation over $1000 in Q1 2026")
    assert res.schema_type == "invoice"
    assert res.field_filters.get("vendor_name") == "Acme Corporation"
    assert res.numeric_ranges.get("total") == (1000.0, None)
    assert res.date_ranges.get("invoice_date") == ("2026-01-01", "2026-03-31")


def test_parse_ambiguous_fallback():
    res_vague = parse_natural_filter("show me good documents")
    assert res_vague.confidence == FilterConfidence.AMBIGUOUS
    assert res_vague.clarification_needed is not None
    assert "clarify" in res_vague.clarification_needed.lower()

    res_empty = parse_natural_filter("")
    assert res_empty.confidence == FilterConfidence.AMBIGUOUS
    assert res_empty.clarification_needed is not None


def test_store_range_filters_safety_and_validation():
    conn = MagicMock()
    tenant_id = uuid4()

    # Disallowed field must raise ValueError
    with pytest.raises(ValueError, match="unsupported extraction filter"):
        list_extractions_with_ranges(conn, tenant_id, field_filters={"malicious_key": "val"})

    with pytest.raises(ValueError, match="unsupported numeric range filter"):
        list_extractions_with_ranges(
            conn, tenant_id, numeric_ranges={"secret_field": (1.0, 10.0)}
        )

    with pytest.raises(ValueError, match="unsupported date range filter"):
        list_extractions_with_ranges(
            conn, tenant_id, date_ranges={"injected_date": ("2026-01-01", None)}
        )


def test_api_natural_filter_endpoint(monkeypatch):
    tenant_id = uuid4()
    doc_id = uuid4()

    fake_row = DocumentExtractionRow(
        document_id=doc_id,
        schema_type="invoice",
        schema_version=1,
        model="gemini-2.5-flash",
        fields={"vendor_name": "Acme Corporation", "total": 1250.0},
        field_confidence={"vendor_name": 0.95, "total": 0.9},
        overall_confidence=0.92,
        needs_review=False,
        created_at="2026-01-15T12:00:00Z",
    )

    def fake_list(connection, tid, **kwargs):
        assert tid == tenant_id
        return [fake_row]

    monkeypatch.setattr(api, "list_extractions_with_ranges", fake_list)
    monkeypatch.setattr(api, "get_connection", lambda: nullcontext(MagicMock()))

    # Valid request
    req = api.NaturalFilterRequest(query="invoices from Acme Corporation over $1000")
    user = (uuid4(), tenant_id, "test@example.com", False)
    resp = api.filter_extractions_natural(req, current_user=user)

    assert len(resp.extractions) == 1
    assert resp.extractions[0].document_id == doc_id
    assert resp.clarification_needed is None
    assert "vendor_name" in resp.applied_filters

    # Ambiguous request returns clarification without error
    ambig_req = api.NaturalFilterRequest(query="show all good stuff")
    ambig_resp = api.filter_extractions_natural(ambig_req, current_user=user)
    assert len(ambig_resp.extractions) == 0
    assert ambig_resp.clarification_needed is not None


def test_api_get_extractions_with_natural_query(monkeypatch):
    tenant_id = uuid4()
    doc_id = uuid4()

    fake_row = DocumentExtractionRow(
        document_id=doc_id,
        schema_type="contract",
        schema_version=1,
        model="gemini-2.5-flash",
        fields={"counterparty": "Globex", "total_value": 75000.0},
        field_confidence={"counterparty": 0.9},
        overall_confidence=0.9,
        needs_review=False,
        created_at="2026-03-01T12:00:00Z",
    )

    def fake_list_ranges(connection, tid, **kwargs):
        assert tid == tenant_id
        return [fake_row]

    monkeypatch.setattr(api, "list_extractions_with_ranges", fake_list_ranges)
    monkeypatch.setattr(api, "get_connection", lambda: nullcontext(MagicMock()))
    user = (uuid4(), tenant_id, "test@example.com", False)

    resp = api.extractions(
        natural_query="contracts over $50k",
        current_user=user,
    )
    assert len(resp.extractions) == 1
    assert resp.extractions[0].document_id == doc_id


def test_natural_filter_against_golden_set_dataset():
    """Verify natural filter against the real evaluation/extraction-golden-set.json dataset."""
    import json
    from pathlib import Path

    golden_path = Path("evaluation/extraction-golden-set.json")
    assert golden_path.exists()
    golden_data = json.loads(golden_path.read_text(encoding="utf-8"))
    documents = golden_data["documents"]

    # Helper to simulate in-memory filtering based on parsed result
    def evaluate(query: str):
        parsed = parse_natural_filter(query)
        matches = []
        for d in documents:
            exp = d["expected"]
            # Check vendor
            if "vendor_name" in parsed.field_filters:
                if exp.get("vendor_name") != parsed.field_filters["vendor_name"]:
                    continue
            # Check currency
            if "currency" in parsed.field_filters:
                if exp.get("currency") != parsed.field_filters["currency"]:
                    continue
            # Check total
            if "total" in parsed.numeric_ranges:
                lo, hi = parsed.numeric_ranges["total"]
                total_raw = exp.get("total")
                if total_raw is None:
                    continue
                if isinstance(total_raw, str):
                    total_val = float(total_raw.replace(",", ""))
                else:
                    total_val = float(total_raw)
                if lo is not None and total_val < lo:
                    continue
                if hi is not None and total_val > hi:
                    continue
            # Check date
            if "invoice_date" in parsed.date_ranges:
                start_d, end_d = parsed.date_ranges["invoice_date"]
                inv_d = exp.get("invoice_date")
                if not inv_d:
                    continue
                if start_d is not None and inv_d < start_d:
                    continue
                if end_d is not None and inv_d > end_d:
                    continue
            matches.append(d["document"])
        return matches

    # 1. Acme invoices
    acme_matches = evaluate("invoices from Acme Corporation")
    assert acme_matches == ["invoice-01-clean"]

    # 2. Invoices over $1000
    over_1000 = evaluate("invoices over $1000")
    assert "invoice-02-multi-line" in over_1000
    assert "invoice-04-messy-scan" in over_1000
    assert "invoice-05-eur" in over_1000
    assert "invoice-01-clean" not in over_1000

    # 3. EUR currency
    eur_matches = evaluate("invoices in EUR")
    assert eur_matches == ["invoice-05-eur"]

    # 4. Invoices in Q1 2026
    q1_matches = evaluate("invoices from Q1 2026")
    assert "invoice-01-clean" in q1_matches
    assert "invoice-02-multi-line" in q1_matches
    assert "invoice-05-eur" in q1_matches

