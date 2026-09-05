"""Extraction worker orchestration: classify, extract, validate, store.

Failure semantics (mirroring ingestion's deliberate-failure design):
- Validation errors (unparseable/invalid model output): one bounded retry with
  a stricter prompt, then a ``failed_extractions`` row and a terminal ``failed``
  job. Never a silent retry loop.
- Transient provider errors: the job is reset to ``queued`` and the error
  propagates so Pub/Sub redelivery retries; the dead-letter topic owns
  exhaustion.
"""

import json
import logging
from io import BytesIO
from uuid import uuid4

from pydantic import ValidationError

from knowledgeforge.config import Settings
from knowledgeforge.db import get_connection
from knowledgeforge.extraction.classifier import (
    ClassificationParseError,
    classify_locally,
    parse_classification,
)
from knowledgeforge.extraction.jobs import ExtractionEvent
from knowledgeforge.extraction.provider import build_provider
from knowledgeforge.extraction.schemas import ExtractionWithConfidence
from knowledgeforge.extraction.store import (
    claim_extraction_job,
    finish_extraction_job,
    get_document_storage_uri,
    has_successful_extraction,
    record_failed_extraction,
    set_document_classification,
    store_document_extraction,
)
from knowledgeforge.ingestion.extract import extract_pdf
from knowledgeforge.ingestion.extract_docx import extract_docx
from knowledgeforge.ingestion.extract_markdown import extract_markdown
from knowledgeforge.ingestion.extract_text import extract_html, extract_text
from knowledgeforge.ingestion.store import record_request_log
from knowledgeforge.worker.cloud import CloudStorageClient

logger = logging.getLogger("knowledgeforge.extraction")

# Documents whose extracted text is below this are treated as scans and go
# through multimodal extraction over the original bytes.
_MIN_TEXT_CHARS = 40

_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".tif", ".tiff")

_MIME_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "pdf": "application/pdf",
}


class ExtractionFailed(ValueError):
    """Bounded-retry-exhausted extraction failure (terminal)."""


def _document_text(content: bytes, filename: str) -> str:
    """Best-effort text view of the original object (never raises)."""
    lowered = filename.lower()
    try:
        if lowered.endswith(".pdf"):
            pages = extract_pdf(BytesIO(content))
        elif lowered.endswith(".docx"):
            pages = extract_docx(BytesIO(content))
        elif lowered.endswith((".html", ".htm")):
            pages = extract_html(BytesIO(content))
        elif lowered.endswith((".txt", ".text")):
            pages = extract_text(BytesIO(content))
        elif lowered.endswith(_IMAGE_SUFFIXES):
            return ""
        else:
            pages = extract_markdown(BytesIO(content))
    except Exception:  # unparseable original: the multimodal path decides
        return ""
    return "\n\n".join(text for _, text in pages)


def _mime_type(filename: str) -> str:
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    return _MIME_TYPES.get(suffix, "application/octet-stream")


def _record_telemetry(
    tenant_id: object, label: str, input_tokens: int, output_tokens: int, settings: Settings
) -> None:
    """Extraction token usage flows into the same request_logs/usage pipeline."""
    cost = (
        input_tokens * settings.gemini_input_token_cost
        + output_tokens * settings.gemini_output_token_cost
    ) / 1_000_000
    try:
        with get_connection() as connection:
            record_request_log(
                connection,
                request_id=uuid4(),
                tenant_id=tenant_id,  # type: ignore[arg-type]
                query=f"[extraction:{label}]",
                retrieved_chunk_ids=[],
                latency_ms=0.0,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_estimate=cost,
            )
    except Exception:
        logger.error("Failed to record extraction telemetry", exc_info=True)


def _classify(
    event: ExtractionEvent, filename: str, text: str, settings: Settings
) -> tuple[str, float]:
    """Cheap local pre-filter first; Gemini only when the verdict is unclear."""
    local = classify_locally(filename, text)
    if local is not None:
        return local.doc_type, local.confidence
    provider = build_provider(settings)
    result = provider.classify(text)
    _record_telemetry(
        event.tenant_id, "classify", result.input_tokens, result.output_tokens, settings
    )
    classification = parse_classification(result.raw_output)
    return classification.doc_type, classification.confidence


def _parse_fields(raw_output: str) -> ExtractionWithConfidence:
    try:
        data = json.loads(raw_output)
    except ValueError as exc:
        raise ExtractionFailed(f"model output is not valid JSON: {exc}") from exc
    return ExtractionWithConfidence.model_validate(data)


def _extract_fields(
    event: ExtractionEvent,
    content: bytes,
    filename: str,
    text: str,
    settings: Settings,
) -> ExtractionWithConfidence:
    provider = build_provider(settings)
    lowered = filename.lower()
    # Scans/images have no text layer: extraction runs over the original bytes
    # as multimodal input (Gemini vision) instead of parsed text.
    needs_multimodal = lowered.endswith(_IMAGE_SUFFIXES) or len(text.strip()) < _MIN_TEXT_CHARS
    errors: list[str] = []
    for attempt in range(2):  # one bounded retry on invalid output
        retry = attempt > 0
        if needs_multimodal:
            result = provider.extract_document(content, _mime_type(filename), retry=retry)
        else:
            result = provider.extract(text, retry=retry)
        _record_telemetry(
            event.tenant_id, "extract", result.input_tokens, result.output_tokens, settings
        )
        try:
            return _parse_fields(result.raw_output)
        except (ValidationError, ExtractionFailed) as exc:
            errors.append(str(exc))
    raise ExtractionFailed(
        "model output failed invoice validation after retry: " + "; ".join(errors[-2:])
    )


def process_extraction_job(event: ExtractionEvent, settings: Settings) -> bool:
    """Process one extraction delivery; unclaimable jobs are acknowledged duplicates.

    Returns whether this caller did the work. Redeliveries of an actively
    processing job (claim lease unexpired) are no-ops; an expired lease (a
    crashed worker) is re-claimable.
    """
    with get_connection() as connection:
        if not claim_extraction_job(connection, event.job_id):
            return False

    try:
        _run_extraction(event, settings)
    except (ExtractionFailed, ClassificationParseError, ValidationError) as exc:
        # Terminal: bounded retries are exhausted. Record and stop — a failed
        # extraction needs human attention, not a silent redelivery loop.
        with get_connection() as connection:
            record_failed_extraction(
                connection,
                tenant_id=event.tenant_id,
                document_id=event.document_id,
                schema_type=event.schema_type,
                raw_output=None,
                error=str(exc),
                attempt_count=1,
            )
            finish_extraction_job(connection, event.job_id, "failed", str(exc)[:500])
        logger.error("extraction.failed document_id=%s error=%s", event.document_id, exc)
        return True
    except Exception:
        # Transient (provider/network): reset so redelivery can re-claim; the
        # dead-letter topic owns retry exhaustion.
        with get_connection() as connection:
            finish_extraction_job(connection, event.job_id, "queued", None)
        logger.error(
            "extraction.transient_failure document_id=%s", event.document_id, exc_info=True
        )
        raise
    return True


def _run_extraction(event: ExtractionEvent, settings: Settings) -> None:
    if event.reason != "reprocess":
        # Idempotency pre-check: never pay for the same (tenant, content,
        # schema, version, model) twice. The UNIQUE constraint is the race
        # backstop, not the primary check.
        with get_connection() as connection:
            if has_successful_extraction(
                connection,
                tenant_id=event.tenant_id,
                content_hash=event.content_hash,
                schema_type=event.schema_type,
                schema_version=event.schema_version,
                model=event.model,
            ):
                finish_extraction_job(connection, event.job_id, "succeeded", "cached")
                return

    with get_connection() as connection:
        uri = get_document_storage_uri(connection, event.document_id, event.tenant_id)
    if uri is None:
        # Not extraction-eligible (synchronous-path document); expected no-op.
        finish_extraction_job(connection, event.job_id, "skipped", "no_stored_original")
        return
    content = CloudStorageClient(settings.gcs_bucket, settings.gcp_project_id).download(uri)
    filename = uri.rsplit("/", 1)[-1]
    text = _document_text(content, filename)

    doc_type, confidence = _classify(event, filename, text, settings)
    with get_connection() as connection:
        set_document_classification(
            connection, event.document_id, event.tenant_id, doc_type, confidence
        )
    if doc_type != "invoice":
        # Most of a tenant's corpus is not invoices; that is expected, not an
        # error state.
        finish_extraction_job(
            connection, event.job_id, "skipped", "unsupported_document_type"
        )
        return

    parsed = _extract_fields(event, content, filename, text, settings)
    confidence_values = list(parsed.field_confidence.values())
    overall = min(confidence_values) if confidence_values else 1.0
    needs_review = overall < settings.extraction_overall_confidence_threshold or any(
        value < settings.extraction_field_confidence_threshold for value in confidence_values
    )
    with get_connection() as connection:
        store_document_extraction(
            connection,
            tenant_id=event.tenant_id,
            document_id=event.document_id,
            content_hash=event.content_hash,
            schema_type=event.schema_type,
            schema_version=event.schema_version,
            model=event.model,
            extraction=parsed.invoice,
            field_confidence=parsed.field_confidence,
            overall_confidence=overall,
            needs_review=needs_review,
            input_tokens=0,
            output_tokens=0,
        )
        finish_extraction_job(connection, event.job_id, "succeeded", None)
    logger.info(
        "extraction.succeeded document_id=%s overall_confidence=%.3f needs_review=%s",
        event.document_id,
        overall,
        needs_review,
    )
