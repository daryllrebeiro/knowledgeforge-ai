"""Extraction persistence: jobs, outbox, extractions, failures, classification."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.types.json import Json

from knowledgeforge.extraction.schemas import InvoiceExtraction

ACTIVE_JOB_FILTER = "status IN ('queued', 'processing')"


@dataclass(frozen=True)
class DocumentExtractionRow:
    document_id: UUID
    schema_type: str
    schema_version: int
    model: str
    fields: dict[str, Any]
    field_confidence: dict[str, float]
    overall_confidence: float
    needs_review: bool
    created_at: str


@dataclass(frozen=True)
class ExtractionJobRow:
    job_id: UUID
    document_id: UUID
    status: str
    reason: str
    schema_type: str
    schema_version: int
    model: str
    detail: str | None
    attempt_count: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class OutboxRow:
    outbox_id: UUID
    job_id: UUID
    # psycopg returns JSONB as a dict; the dispatcher serializes for publish.
    payload: dict[str, Any]


# ---------------------------------------------------------------------------
# Job creation and lifecycle
# ---------------------------------------------------------------------------


def insert_extraction_job(
    connection: Connection,
    *,
    document_id: UUID,
    tenant_id: UUID,
    content_hash: str,
    schema_type: str,
    schema_version: int,
    model: str,
    reason: str = "ready",
) -> UUID | None:
    """Create an extraction job plus its outbox event in one transaction.

    The insert is conditional on a stored original (``storage_uri IS NOT
    NULL``): synchronous-path documents are searchable but never
    extraction-eligible. The partial unique index makes a second active job a
    no-op — returns ``None`` so callers can map it to a 409 (reprocess) or
    ignore it (duplicate ready delivery).
    """
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO extraction_jobs
                    (tenant_id, document_id, content_hash, schema_type,
                     schema_version, model, reason)
                SELECT %s, %s, %s, %s, %s, %s, %s
                WHERE EXISTS (
                    SELECT 1 FROM documents
                    WHERE id = %s AND tenant_id = %s AND storage_uri IS NOT NULL
                )
                ON CONFLICT (document_id) WHERE status IN ('queued', 'processing')
                DO NOTHING
                RETURNING id
                """,
                (
                    tenant_id,
                    document_id,
                    content_hash,
                    schema_type,
                    schema_version,
                    model,
                    reason,
                    document_id,
                    tenant_id,
                ),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            job_id = UUID(str(row[0]))
            cursor.execute(
                """
                INSERT INTO extraction_outbox (job_id, tenant_id, payload)
                VALUES (%s, %s, %s)
                """,
                (
                    job_id,
                    tenant_id,
                    Json(
                        {
                            "job_id": str(job_id),
                            "document_id": str(document_id),
                            "tenant_id": str(tenant_id),
                            "content_hash": content_hash,
                            "schema_type": schema_type,
                            "schema_version": schema_version,
                            "model": model,
                            "reason": reason,
                        }
                    ),
                ),
            )
    return job_id


# Matches the ingestion claim lease: a crashed extraction worker's job becomes
# re-claimable after ten minutes.
EXTRACTION_JOB_LEASE = "interval '10 minutes'"


def claim_extraction_job(connection: Connection, job_id: UUID) -> bool:
    """Atomically claim a queued job; redeliveries of an active job are no-ops.

    Admits ``queued`` jobs and ``processing`` jobs whose lease has expired, so
    Pub/Sub at-least-once redelivery cannot double-process a job while a
    crashed worker's claim can be re-taken.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE extraction_jobs SET status = 'processing', updated_at = now(), "
            "attempt_count = attempt_count + 1 "
            "WHERE id = %s AND (status = 'queued' OR (status = 'processing' "
            f"AND updated_at < now() - {EXTRACTION_JOB_LEASE}))",
            (job_id,),
        )
        return cursor.rowcount == 1


def finish_extraction_job(
    connection: Connection, job_id: UUID, status: str, detail: str | None = None
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE extraction_jobs SET status = %s, detail = %s, updated_at = now() "
            "WHERE id = %s",
            (status, detail, job_id),
        )


def get_extraction_job(
    connection: Connection, job_id: UUID, tenant_id: UUID
) -> ExtractionJobRow | None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, document_id, status, reason, schema_type, schema_version, "
            "model, detail, attempt_count, created_at, updated_at "
            "FROM extraction_jobs WHERE id = %s AND tenant_id = %s",
            (job_id, tenant_id),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    return ExtractionJobRow(
        job_id=UUID(str(row[0])),
        document_id=UUID(str(row[1])),
        status=str(row[2]),
        reason=str(row[3]),
        schema_type=str(row[4]),
        schema_version=int(row[5]),
        model=str(row[6]),
        detail=None if row[7] is None else str(row[7]),
        attempt_count=int(row[8]),
        created_at=str(row[9]),
        updated_at=str(row[10]),
    )


def has_active_extraction_job(connection: Connection, document_id: UUID) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT 1 FROM extraction_jobs WHERE document_id = %s AND {ACTIVE_JOB_FILTER}",
            (document_id,),
        )
        return cursor.fetchone() is not None


# ---------------------------------------------------------------------------
# Outbox dispatch
# ---------------------------------------------------------------------------


def claim_outbox_batch(
    connection: Connection, *, batch_size: int, lease_seconds: int
) -> list[OutboxRow]:
    """Claim unsent outbox rows with a lease so concurrent jobs cannot double-publish."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE extraction_outbox
            SET claimed_until = now() + (%s * interval '1 second'),
                attempts = attempts + 1
            WHERE id IN (
                SELECT id FROM extraction_outbox
                WHERE sent_at IS NULL
                  AND (claimed_until IS NULL OR claimed_until < now())
                ORDER BY created_at
                LIMIT %s
                FOR UPDATE SKIP LOCKED
            )
            RETURNING id, job_id, payload
            """,
            (lease_seconds, batch_size),
        )
        rows = cursor.fetchall()
    return [
        OutboxRow(
            outbox_id=UUID(str(row[0])),
            job_id=UUID(str(row[1])),
            payload=dict(row[2]) if not isinstance(row[2], dict) else row[2],
        )
        for row in rows
    ]


def mark_outbox_sent(connection: Connection, outbox_id: UUID) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE extraction_outbox SET sent_at = now(), claimed_until = NULL "
            "WHERE id = %s",
            (outbox_id,),
        )


def count_stuck_outbox_rows(connection: Connection, *, older_than_seconds: int) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM extraction_outbox "
            "WHERE sent_at IS NULL AND created_at < now() - (%s * interval '1 second')",
            (older_than_seconds,),
        )
        row = cursor.fetchone()
    return int(row[0]) if row is not None else 0


# ---------------------------------------------------------------------------
# Classification, extractions, failures
# ---------------------------------------------------------------------------


def set_document_classification(
    connection: Connection,
    document_id: UUID,
    tenant_id: UUID,
    doc_type: str,
    confidence: float,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE documents SET detected_doc_type = %s, doc_type_confidence = %s "
            "WHERE id = %s AND tenant_id = %s",
            (doc_type, confidence, document_id, tenant_id),
        )


def has_successful_extraction(
    connection: Connection,
    *,
    tenant_id: UUID,
    content_hash: str,
    schema_type: str,
    schema_version: int,
    model: str,
) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM document_extractions "
            "WHERE tenant_id = %s AND content_hash = %s AND schema_type = %s "
            "AND schema_version = %s AND model = %s",
            (tenant_id, content_hash, schema_type, schema_version, model),
        )
        return cursor.fetchone() is not None


def store_document_extraction(
    connection: Connection,
    *,
    tenant_id: UUID,
    document_id: UUID,
    content_hash: str,
    schema_type: str,
    schema_version: int,
    model: str,
    extraction: InvoiceExtraction,
    field_confidence: dict[str, float],
    overall_confidence: float,
    needs_review: bool,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Insert (or, on reprocess, replace) the successful extraction row.

    The unique key is the idempotency mechanism: an unchanged document never
    triggers a second paid call because the worker checks for an existing row
    first, and this upsert is the replace-after-validation semantics a forced
    reprocess needs.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO document_extractions
                (tenant_id, document_id, content_hash, schema_type, schema_version,
                 model, fields, field_confidence, overall_confidence, needs_review,
                 input_tokens, output_tokens)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, content_hash, schema_type, schema_version, model)
            DO UPDATE SET
                document_id = EXCLUDED.document_id,
                fields = EXCLUDED.fields,
                field_confidence = EXCLUDED.field_confidence,
                overall_confidence = EXCLUDED.overall_confidence,
                needs_review = EXCLUDED.needs_review,
                input_tokens = EXCLUDED.input_tokens,
                output_tokens = EXCLUDED.output_tokens,
                created_at = now()
            """,
            (
                tenant_id,
                document_id,
                content_hash,
                schema_type,
                schema_version,
                model,
                Json(extraction.model_dump(mode="json")),
                Json(field_confidence),
                overall_confidence,
                needs_review,
                input_tokens,
                output_tokens,
            ),
        )


def record_failed_extraction(
    connection: Connection,
    *,
    tenant_id: UUID,
    document_id: UUID,
    schema_type: str,
    raw_output: str | None,
    error: str,
    attempt_count: int,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO failed_extractions
                (tenant_id, document_id, schema_type, raw_output, error, attempt_count)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (tenant_id, document_id, schema_type, raw_output, error, attempt_count),
        )


def get_document_extraction(
    connection: Connection, document_id: UUID, tenant_id: UUID
) -> DocumentExtractionRow | None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT document_id, schema_type, schema_version, model, fields, "
            "field_confidence, overall_confidence, needs_review, created_at "
            "FROM document_extractions WHERE document_id = %s AND tenant_id = %s "
            "ORDER BY created_at DESC LIMIT 1",
            (document_id, tenant_id),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    return DocumentExtractionRow(
        document_id=UUID(str(row[0])),
        schema_type=str(row[1]),
        schema_version=int(row[2]),
        model=str(row[3]),
        fields=dict(row[4]) if row[4] is not None else {},
        field_confidence=dict(row[5]) if row[5] is not None else {},
        overall_confidence=float(row[6]),
        needs_review=bool(row[7]),
        created_at=str(row[8]),
    )


# JSONB field filters are allow-listed and parameterized; arbitrary query keys
# can never reach the SQL.
EXTRACTION_FIELD_FILTERS = ("vendor_name", "invoice_number", "currency")


def list_extractions(
    connection: Connection,
    tenant_id: UUID,
    *,
    schema_type: str | None = None,
    field_filters: dict[str, str] | None = None,
    needs_review: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[DocumentExtractionRow]:
    if limit <= 0 or offset < 0:
        raise ValueError("limit must be positive and offset non-negative")
    clauses = ["e.tenant_id = %s"]
    params: list[object] = [tenant_id]
    if schema_type is not None:
        clauses.append("e.schema_type = %s")
        params.append(schema_type)
    if needs_review is not None:
        clauses.append("e.needs_review = %s")
        params.append(needs_review)
    for field, value in (field_filters or {}).items():
        if field not in EXTRACTION_FIELD_FILTERS:
            raise ValueError(f"unsupported extraction filter: {field}")
        clauses.append("e.fields->>%s = %s")
        params.extend([field, value])
    where = " AND ".join(clauses)
    params.extend([limit, offset])
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT e.document_id, e.schema_type, e.schema_version, e.model, e.fields,
                   e.field_confidence, e.overall_confidence, e.needs_review, e.created_at
            FROM document_extractions AS e
            WHERE {where}
            ORDER BY e.created_at DESC
            LIMIT %s OFFSET %s
            """,
            tuple(params),
        )
        rows = cursor.fetchall()
    return [
        DocumentExtractionRow(
            document_id=UUID(str(row[0])),
            schema_type=str(row[1]),
            schema_version=int(row[2]),
            model=str(row[3]),
            fields=dict(row[4]) if row[4] is not None else {},
            field_confidence=dict(row[5]) if row[5] is not None else {},
            overall_confidence=float(row[6]),
            needs_review=bool(row[7]),
            created_at=str(row[8]),
        )
        for row in rows
    ]


def find_document_ids_by_fields(
    connection: Connection,
    tenant_id: UUID,
    *,
    schema_type: str | None = None,
    field_filters: dict[str, str] | None = None,
) -> list[UUID]:
    """Resolve structured_filters to document IDs (the /ask pre-step)."""
    clauses = ["e.tenant_id = %s"]
    params: list[object] = [tenant_id]
    if schema_type is not None:
        clauses.append("e.schema_type = %s")
        params.append(schema_type)
    for field, value in (field_filters or {}).items():
        if field not in EXTRACTION_FIELD_FILTERS:
            raise ValueError(f"unsupported extraction filter: {field}")
        clauses.append("e.fields->>%s = %s")
        params.extend([field, value])
    where = " AND ".join(clauses)
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT DISTINCT e.document_id FROM document_extractions AS e WHERE {where}",
            tuple(params),
        )
        rows = cursor.fetchall()
    return [UUID(str(row[0])) for row in rows]


def get_document_storage_uri(
    connection: Connection, document_id: UUID, tenant_id: UUID
) -> str | None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT storage_uri FROM documents WHERE id = %s AND tenant_id = %s",
            (document_id, tenant_id),
        )
        row = cursor.fetchone()
    return None if row is None or row[0] is None else str(row[0])
