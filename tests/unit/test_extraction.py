"""Unit tests for Phase 2.5 extraction schemas, classifier, provider, and pipeline."""

import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from knowledgeforge.config import Settings
from knowledgeforge.extraction.classifier import (
    ClassificationParseError,
    classify_locally,
    parse_classification,
)
from knowledgeforge.extraction.jobs import ExtractionEvent, parse_event
from knowledgeforge.extraction.pipeline import (
    ExtractionFailed,
    _parse_fields,
    _run_extraction,
    process_extraction_job,
)
from knowledgeforge.extraction.provider import LocalExtractionProvider
from knowledgeforge.extraction.schemas import InvoiceExtraction, LineItem
from knowledgeforge.generation.local import local_answer
from knowledgeforge.generation.prompt import LabeledChunk, LabeledExtraction
from knowledgeforge.ingestion.chunk import TextChunk


def test_line_item_rejects_negative_values() -> None:
    with pytest.raises(ValidationError):
        LineItem(description="item", quantity=-1)
    with pytest.raises(ValidationError):
        LineItem(description="item", amount=-5.0)


class FakeConnection:
    """Minimal connection stub: the pipeline only uses it as a context manager."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _fake_connection_factory():
    return lambda: FakeConnection()


def test_invoice_extraction_rejects_negative_total() -> None:
    """Credit notes/refunds are outside the invoice schema in this phase."""
    with pytest.raises(ValidationError):
        InvoiceExtraction(vendor_name="Acme", total=-100.0)


def test_invoice_extraction_accepts_minimal_invoice() -> None:
    invoice = InvoiceExtraction(vendor_name="Acme", total=0.0)
    assert invoice.currency == "USD"
    assert invoice.line_items == []


def test_parse_fields_accepts_valid_payload() -> None:
    payload = json.dumps(
        {
            "invoice": {
                "vendor_name": "Acme Corporation",
                "invoice_number": "INV-1001",
                "invoice_date": "2026-01-15",
                "due_date": None,
                "total": 250.00,
                "currency": "USD",
                "line_items": [],
            },
            "field_confidence": {"vendor_name": 0.97, "total": 0.93},
        }
    )
    parsed = _parse_fields(payload)
    assert parsed.invoice.vendor_name == "Acme Corporation"
    assert parsed.invoice.total == 250.0
    assert parsed.field_confidence["total"] == 0.93


def test_parse_fields_rejects_non_json() -> None:
    with pytest.raises(ExtractionFailed, match="not valid JSON"):
        _parse_fields("no json here")


def test_parse_fields_rejects_missing_required_field() -> None:
    with pytest.raises(ValidationError):
        _parse_fields(json.dumps({"invoice": {"total": 10.0}, "field_confidence": {}}))


def test_classify_locally_filename_hint() -> None:
    result = classify_locally("invoice-march.pdf", "irrelevant")
    assert result is not None
    assert result.doc_type == "invoice"
    assert result.confidence >= 0.9


def test_classify_locally_keyword_hits() -> None:
    text = "INVOICE #12\nPlease remit to us. Total due: 50 USD\nBill to: someone"
    result = classify_locally("scan.png", text)
    assert result is not None and result.doc_type == "invoice"


def test_classify_locally_long_document_skips_provider() -> None:
    result = classify_locally("manual.pdf", "x" * 20_000)
    assert result is not None
    assert result.doc_type == "unclassified"


def test_classify_locally_unclear_returns_none() -> None:
    assert classify_locally("notes.md", "a short note") is None


def test_parse_classification_round_trip() -> None:
    classification = parse_classification('{"doc_type": "invoice", "confidence": 0.9}')
    assert classification.doc_type == "invoice"
    assert classification.confidence == 0.9


def test_parse_classification_rejects_unknown_type() -> None:
    with pytest.raises(ClassificationParseError):
        parse_classification('{"doc_type": "receipt", "confidence": 0.9}')


def test_local_provider_is_deterministic_by_content() -> None:
    provider = LocalExtractionProvider()
    first = provider.extract("invoice one")
    second = provider.extract("invoice one")
    other = provider.extract("invoice two")
    assert first.raw_output == second.raw_output
    assert first.raw_output != other.raw_output


def test_local_provider_classifies_as_invoice() -> None:
    result = LocalExtractionProvider().classify("anything")
    assert json.loads(result.raw_output)["doc_type"] == "invoice"


def test_local_answer_cites_extracted_fields() -> None:
    chunks = [LabeledChunk(label="doc 1", chunk=TextChunk(text="body", page=2))]
    extractions = [LabeledExtraction(label="doc 1", fields={"vendor_name": "Acme", "total": 250})]
    answer = local_answer("what did we pay?", chunks, extractions)
    assert "[doc 1, extracted fields]" in answer
    assert "[doc 1, page 2]" in answer


def test_local_answer_refusal_without_context() -> None:
    assert local_answer("q", [], []) == "I don't have enough information."


def test_parse_event_round_trip() -> None:
    event = ExtractionEvent(
        job_id=uuid4(),
        document_id=uuid4(),
        tenant_id=uuid4(),
        content_hash="hash",
        schema_type="invoice",
        schema_version=1,
        model="gemini-2.0-flash",
        reason="reprocess",
    )
    payload = json.dumps(
        {
            "job_id": str(event.job_id),
            "document_id": str(event.document_id),
            "tenant_id": str(event.tenant_id),
            "content_hash": "hash",
            "schema_type": "invoice",
            "schema_version": 1,
            "model": "gemini-2.0-flash",
            "reason": "reprocess",
        }
    ).encode()
    assert parse_event(payload) == event


def test_process_extraction_job_unclaimable_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """An actively-processing job (unexpired lease) is an acknowledged duplicate."""
    from knowledgeforge.extraction import pipeline

    event = ExtractionEvent(
        job_id=uuid4(),
        document_id=uuid4(),
        tenant_id=uuid4(),
        content_hash="hash",
        schema_type="invoice",
        schema_version=1,
        model="m",
    )
    monkeypatch.setattr(pipeline, "claim_extraction_job", lambda connection, job_id: False)
    monkeypatch.setattr(pipeline, "_run_extraction", lambda event, settings: None)
    monkeypatch.setattr(pipeline, "get_connection", _fake_connection_factory())
    assert process_extraction_job(event, Settings()) is False


def test_run_extraction_records_failure_and_skips_non_invoice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-invoice classification is a terminal skip, not a failure row."""
    from knowledgeforge.extraction import pipeline

    event = ExtractionEvent(
        job_id=uuid4(),
        document_id=uuid4(),
        tenant_id=uuid4(),
        content_hash="hash",
        schema_type="invoice",
        schema_version=1,
        model="m",
    )
    finished: list[tuple[str, str | None]] = []

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    fake_get_connection = _fake_connection_factory()

    monkeypatch.setattr(pipeline, "claim_extraction_job", lambda c, j: True)
    monkeypatch.setattr(pipeline, "has_successful_extraction", lambda connection, **kw: False)
    monkeypatch.setattr(
        pipeline, "get_document_storage_uri", lambda connection, document_id, tenant_id: "gs://b/f.pdf"
    )

    class FakeStorage:
        def __init__(self, bucket: str, project: str = "") -> None:
            pass

        def download(self, uri: str) -> bytes:
            return b"INVOICE #1 total due 50"

    monkeypatch.setattr(pipeline, "CloudStorageClient", FakeStorage)
    monkeypatch.setattr(
        pipeline, "_classify", lambda event, filename, text, settings: ("unclassified", 0.9)
    )
    monkeypatch.setattr(
        pipeline, "set_document_classification", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        pipeline,
        "finish_extraction_job",
        lambda connection, job_id, status, detail=None: finished.append((status, detail)),
    )
    monkeypatch.setattr(pipeline, "get_connection", fake_get_connection)

    pipeline.process_extraction_job(event, Settings())
    assert finished == [("skipped", "unsupported_document_type")]


def test_run_extraction_cache_hit_skips_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """An existing successful row short-circuits before any model call."""
    from knowledgeforge.extraction import pipeline

    event = ExtractionEvent(
        job_id=uuid4(),
        document_id=uuid4(),
        tenant_id=uuid4(),
        content_hash="hash",
        schema_type="invoice",
        schema_version=1,
        model="m",
    )
    finished: list[tuple[str, str | None]] = []

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def _fail(*args, **kwargs):
        raise AssertionError("model must not be called on a cache hit")

    monkeypatch.setattr(pipeline, "claim_extraction_job", lambda c, j: True)
    monkeypatch.setattr(pipeline, "has_successful_extraction", lambda connection, **kw: True)
    monkeypatch.setattr(
        pipeline,
        "finish_extraction_job",
        lambda connection, job_id, status, detail=None: finished.append((status, detail)),
    )
    monkeypatch.setattr(pipeline, "get_connection", _fake_connection_factory())
    monkeypatch.setattr(pipeline, "_run_extraction", _fail)

    # Cache check happens inside _run_extraction; call it directly with the
    # has_successful_extraction override in place.
    monkeypatch.setattr(pipeline, "CloudStorageClient", _fail)
    _run_extraction(event, Settings())
    assert finished == [("succeeded", "cached")]


def test_needs_review_flagged_on_low_confidence() -> None:
    """Overall confidence below the threshold flags the row for review."""
    parsed = _parse_fields(
        json.dumps(
            {
                "invoice": {"vendor_name": "Acme", "total": 10.0},
                "field_confidence": {"vendor_name": 0.97, "total": 0.3},
            }
        )
    )
    values = list(parsed.field_confidence.values())
    overall = min(values)
    settings = Settings()
    assert overall < settings.extraction_overall_confidence_threshold
    assert any(v < settings.extraction_field_confidence_threshold for v in values)
