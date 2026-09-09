import pytest
from pydantic import ValidationError

from knowledgeforge.extraction.dynamic_schemas import (
    compile_json_schema_to_pydantic,
    infer_schema_from_sample,
)


def test_compile_json_schema_to_pydantic_validates() -> None:
    json_schema = {
        "title": "VendorContract",
        "type": "object",
        "properties": {
            "vendor_name": {"type": "string"},
            "contract_value": {"type": "number"},
            "auto_renew": {"type": "boolean"},
            "term_years": {"type": "integer"},
        },
        "required": ["vendor_name", "contract_value"],
    }

    model_cls = compile_json_schema_to_pydantic("vendor_contract", json_schema)
    assert model_cls.__name__ == "VendorContract"

    # Valid instance
    instance = model_cls(
        vendor_name="Acme Inc",
        contract_value=45000.50,
        auto_renew=True,
        term_years=3,
    )
    assert instance.vendor_name == "Acme Inc"
    assert instance.contract_value == 45000.50
    assert instance.auto_renew is True
    assert instance.term_years == 3

    # Missing required field raises ValidationError
    with pytest.raises(ValidationError):
        model_cls(contract_value=100.0)


def test_infer_schema_from_sample_financial_contract() -> None:
    sample = """
    MASTER SERVICES AGREEMENT
    This Agreement is entered into on 2025-04-15 between CloudTech Inc. and DataCorp LLC.
    The total contract value shall not exceed $120,000 USD paid over 12 months.
    """
    inferred = infer_schema_from_sample(sample, default_name="msa_schema")
    assert inferred["title"] == "msa_schema"
    props = inferred["properties"]
    assert "document_date" in props
    assert "total_amount" in props
    assert "party_a" in props
    assert props["total_amount"]["type"] == "number"


def test_infer_schema_from_sample_fallback() -> None:
    sample = "Just some plain text without dates or currency."
    inferred = infer_schema_from_sample(sample)
    props = inferred["properties"]
    assert "title" in props
    assert "summary" in props
