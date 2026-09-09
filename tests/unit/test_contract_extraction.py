"""Unit tests for Item 9: Second Extraction Schema (Contract Extraction)."""

import json
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from knowledgeforge import api
from knowledgeforge.extraction.classifier import (
    classify_locally,
    looks_like_contract_text,
    parse_classification,
)
from knowledgeforge.extraction.pipeline import _parse_fields
from knowledgeforge.extraction.provider import LocalExtractionProvider
from knowledgeforge.extraction.schemas import (
    ContractExtraction,
    ContractExtractionWithConfidence,
    render_fields,
)
from knowledgeforge.extraction.store import (
    DocumentExtractionRow,
)
from knowledgeforge.main import app

# ---------------------------------------------------------------------------
# Schema and Model Validation Tests
# ---------------------------------------------------------------------------


def test_contract_extraction_valid_model():
    contract = ContractExtraction(
        counterparty="Global Logistics Corp",
        effective_date=date(2026, 1, 1),
        termination_date=date(2027, 1, 1),
        total_value=150000.0,
        currency="USD",
        governing_law="Delaware",
        auto_renew=True,
    )
    assert contract.counterparty == "Global Logistics Corp"
    assert contract.effective_date == date(2026, 1, 1)
    assert contract.total_value == 150000.0
    assert contract.auto_renew is True


def test_contract_extraction_rejects_negative_value():
    with pytest.raises(ValidationError):
        ContractExtraction(
            counterparty="Bad Corp",
            total_value=-100.0,
        )


def test_contract_extraction_requires_counterparty():
    with pytest.raises(ValidationError):
        ContractExtraction(counterparty="")


def test_contract_extraction_with_confidence():
    raw = {
        "contract": {
            "counterparty": "Nexus Dynamics LLC",
            "effective_date": "2026-03-01",
            "termination_date": "2027-03-01",
            "total_value": 50000.0,
            "currency": "USD",
            "governing_law": "California",
            "auto_renew": False,
        },
        "field_confidence": {
            "counterparty": 0.95,
            "effective_date": 0.90,
            "total_value": 0.88,
        },
    }
    parsed = ContractExtractionWithConfidence.model_validate(raw)
    assert parsed.contract.counterparty == "Nexus Dynamics LLC"
    assert parsed.field_confidence["counterparty"] == 0.95


def test_render_fields_contract():
    fields = {
        "counterparty": "Acme Corp",
        "effective_date": "2026-01-01",
        "total_value": 100000.0,
        "currency": "USD",
        "governing_law": "New York",
        "auto_renew": True,
    }
    rendered = render_fields(fields)
    assert "counterparty: Acme Corp" in rendered
    assert "governing_law: New York" in rendered
    assert "auto_renew: True" in rendered


# ---------------------------------------------------------------------------
# Classification Tests
# ---------------------------------------------------------------------------


def test_classifier_detects_contract_filename():
    classification = classify_locally("master_services_agreement.pdf", "Some random text.")
    assert classification is not None
    assert classification.doc_type == "contract"
    assert classification.confidence == 0.95


def test_classifier_detects_contract_keywords():
    text = (
        "This Agreement is entered into by the parties hereto on the effective date. "
        "The governing law shall be Delaware and termination date is 2027-01-01."
    )
    classification = classify_locally("document.pdf", text)
    assert classification is not None
    assert classification.doc_type == "contract"
    assert classification.confidence == 0.9


def test_looks_like_contract_text():
    assert looks_like_contract_text("Master services agreement terms and conditions.")
    assert not looks_like_contract_text("A recipe for chocolate chip cookies.")


def test_parse_classification_contract():
    res = parse_classification('{"doc_type": "contract", "confidence": 0.92}')
    assert res.doc_type == "contract"
    assert res.confidence == 0.92


# ---------------------------------------------------------------------------
# Provider and Pipeline Tests
# ---------------------------------------------------------------------------


def test_local_provider_extracts_contract():
    provider = LocalExtractionProvider()
    res = provider.extract("Some agreement text", schema_type="contract")
    data = json.loads(res.raw_output)
    assert "contract" in data
    assert data["contract"]["counterparty"] == "Global Logistics Corp"
    assert "field_confidence" in data


def test_pipeline_parse_fields_contract():
    raw = json.dumps(
        {
            "contract": {
                "counterparty": "Alpha Corp",
                "effective_date": "2026-05-01",
                "total_value": 25000.0,
                "currency": "USD",
                "governing_law": "Delaware",
                "auto_renew": False,
            },
            "field_confidence": {"counterparty": 0.98},
        }
    )
    parsed = _parse_fields(raw, schema_type="contract")
    assert isinstance(parsed.contract, ContractExtraction)
    assert parsed.contract.counterparty == "Alpha Corp"
    assert parsed.field_confidence["counterparty"] == 0.98


# ---------------------------------------------------------------------------
# Golden Set Validation Test
# ---------------------------------------------------------------------------


def test_contract_golden_set_structure_and_completeness():
    golden_path = Path(__file__).parents[2] / "evaluation" / "contract-golden-set.json"
    assert golden_path.exists(), "contract-golden-set.json must exist"

    data = json.loads(golden_path.read_text(encoding="utf-8"))
    assert data["schema_type"] == "contract"
    docs = data["documents"]
    assert len(docs) == 20, f"Expected 20 contract golden set docs, got {len(docs)}"

    for doc in docs:
        expected = doc["expected"]
        # Validate that each expected object parses cleanly into ContractExtraction
        parsed = ContractExtraction.model_validate(expected)
        assert len(parsed.counterparty) > 0
        assert doc["text"] is not None
        assert len(doc["text"]) > 20


# ---------------------------------------------------------------------------
# API Filtering for Contracts
# ---------------------------------------------------------------------------


def test_api_list_extractions_with_contract_filters(monkeypatch):
    tenant_id = uuid4()
    user_id = uuid4()
    doc_id = uuid4()

    mock_row = DocumentExtractionRow(
        document_id=doc_id,
        schema_type="contract",
        schema_version=1,
        model="gemini-2.0-flash",
        fields={
            "counterparty": "Acme Corp",
            "total_value": 100000.0,
            "governing_law": "Delaware",
        },
        field_confidence={"counterparty": 0.95},
        overall_confidence=0.95,
        needs_review=False,
        created_at="2026-09-08T00:00:00+00:00",
    )

    class FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_list(connection, tid, **kwargs):
        assert tid == tenant_id
        assert kwargs.get("schema_type") == "contract"
        field_filters = kwargs.get("field_filters", {})
        assert field_filters.get("counterparty") == "Acme Corp"
        assert field_filters.get("governing_law") == "Delaware"
        return [mock_row]

    monkeypatch.setattr(api, "get_connection", lambda: FakeConn())
    monkeypatch.setattr(api, "list_extractions", fake_list)
    app.dependency_overrides[api.get_current_user] = lambda: (user_id, tenant_id, "member", False)

    client = TestClient(app)
    res = client.get(
        "/extractions",
        params={
            "schema_type": "contract",
            "counterparty": "Acme Corp",
            "governing_law": "Delaware",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data["extractions"]) == 1
    ext = data["extractions"][0]
    assert ext["schema_type"] == "contract"
    assert ext["fields"]["counterparty"] == "Acme Corp"
