from collections.abc import Sequence
from typing import NamedTuple
from uuid import UUID

from pgvector.psycopg import register_vector
from psycopg import Connection

from knowledgeforge.ingestion.chunk import TextChunk


class DocumentSummaryRow(NamedTuple):
    document_id: UUID
    title: str
    doc_type: str
    status: str
    version: int
    superseded_by: str | None


class DocumentDetailRow(NamedTuple):
    document_id: UUID
    title: str
    filename: str
    doc_type: str
    status: str
    version: int
    superseded_by: str | None
    chunk_count: int


def store_document(
    connection: Connection,
    *,
    title: str,
    source_filename: str,
    doc_type: str,
    chunks: Sequence[TextChunk],
    embeddings: Sequence[Sequence[float]],
    content_hash: str = "",
    version: int = 1,
    tenant_id: UUID | None = None,
) -> UUID:
    """Insert a document and all chunks atomically, returning the document ID."""
    if len(chunks) != len(embeddings):
        raise ValueError("chunks and embeddings must have the same length")

    register_vector(connection)
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO documents (
                    title, source_filename, doc_type, content_hash, version, tenant_id
                ) VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (title, source_filename, doc_type, content_hash, version, tenant_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("document insert did not return an ID")
            document_id = UUID(str(row[0]))
            cursor.executemany(
                """
                INSERT INTO chunks (document_id, page, section, chunk_text, embedding)
                VALUES (%s, %s, %s, %s, %s)
                """,
                [
                    (document_id, chunk.page, chunk.section, chunk.text, list(embedding))
                    for chunk, embedding in zip(chunks, embeddings, strict=True)
                ],
            )
    return document_id


def create_pending_document(
    connection: Connection,
    *,
    title: str,
    source_filename: str,
    doc_type: str,
    content_hash: str,
    storage_uri: str,
    tenant_id: UUID,
    version: int = 1,
) -> UUID:
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO documents (
                    title, source_filename, doc_type, content_hash, version,
                    tenant_id, status, storage_uri
                ) VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s)
                RETURNING id
                """,
                (
                    title,
                    source_filename,
                    doc_type,
                    content_hash,
                    version,
                    tenant_id,
                    storage_uri,
                ),
            )
            row = cursor.fetchone()
    if row is None:
        raise RuntimeError("pending document insert did not return an ID")
    return UUID(str(row[0]))


def store_chunks(
    connection: Connection,
    document_id: UUID,
    chunks: Sequence[TextChunk],
    embeddings: Sequence[Sequence[float]],
) -> None:
    if len(chunks) != len(embeddings):
        raise ValueError("chunks and embeddings must have the same length")
    register_vector(connection)
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO chunks (document_id, page, section, chunk_text, embedding)
                VALUES (%s, %s, %s, %s, %s)
                """,
                [
                    (document_id, chunk.page, chunk.section, chunk.text, list(embedding))
                    for chunk, embedding in zip(chunks, embeddings, strict=True)
                ],
            )
            cursor.execute(
                "UPDATE documents SET status = 'ready', status_changed_at = now() WHERE id = %s",
                (document_id,),
            )


def get_document_status(connection: Connection, document_id: UUID, tenant_id: UUID) -> str | None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT status FROM documents WHERE id = %s AND tenant_id = %s",
            (document_id, tenant_id),
        )
        row = cursor.fetchone()
    return None if row is None else str(row[0])


def list_documents(
    connection: Connection,
    tenant_id: UUID,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[DocumentSummaryRow]:
    """Return document summaries newest first."""
    if limit <= 0 or offset < 0:
        raise ValueError("limit must be positive and offset non-negative")
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, title, doc_type, status, version, superseded_by
            FROM documents
            WHERE tenant_id = %s
            ORDER BY created_at DESC, id DESC
            LIMIT %s OFFSET %s
            """,
            (tenant_id, limit, offset),
        )
        rows = cursor.fetchall()
    return [
        DocumentSummaryRow(
            document_id=UUID(str(row[0])),
            title=str(row[1]),
            doc_type=str(row[2]),
            status=str(row[3]),
            version=int(row[4]),
            superseded_by=None if row[5] is None else str(row[5]),
        )
        for row in rows
    ]


def get_document_detail(
    connection: Connection, document_id: UUID, tenant_id: UUID
) -> DocumentDetailRow | None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT d.id, d.title, d.source_filename, d.doc_type, d.status, d.version,
                   d.superseded_by, count(c.id) AS chunk_count
            FROM documents AS d
            LEFT JOIN chunks AS c ON c.document_id = d.id
            WHERE d.id = %s AND d.tenant_id = %s
            GROUP BY d.id
            """,
            (document_id, tenant_id),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    return DocumentDetailRow(
        document_id=UUID(str(row[0])),
        title=str(row[1]),
        filename=str(row[2]),
        doc_type=str(row[3]),
        status=str(row[4]),
        version=int(row[5]),
        superseded_by=None if row[6] is None else str(row[6]),
        chunk_count=int(row[7]),
    )


class ChunkPreviewRow(NamedTuple):
    page: int
    section: str | None
    text: str


def list_document_chunks(
    connection: Connection,
    document_id: UUID,
    tenant_id: UUID,
    *,
    limit: int = 20,
    offset: int = 0,
) -> list[ChunkPreviewRow] | None:
    """Preview what was indexed for one document ("what did we index?").

    Returns None when the document does not belong to the tenant.
    """
    if limit <= 0 or offset < 0:
        raise ValueError("limit must be positive and offset non-negative")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM documents WHERE id = %s AND tenant_id = %s",
            (document_id, tenant_id),
        )
        if cursor.fetchone() is None:
            return None
        cursor.execute(
            """
            SELECT page, section, chunk_text FROM chunks
            WHERE document_id = %s
            ORDER BY page, id
            LIMIT %s OFFSET %s
            """,
            (document_id, limit, offset),
        )
        rows = cursor.fetchall()
    return [
        ChunkPreviewRow(
            page=int(row[0]), section=None if row[1] is None else str(row[1]), text=str(row[2])
        )
        for row in rows
    ]


def get_document_ingest_info(
    connection: Connection, document_id: UUID, tenant_id: UUID
) -> tuple[str, str | None, str] | None:
    """Return (status, storage_uri, content_hash) for re-ingestion decisions."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT status, storage_uri, content_hash FROM documents "
            "WHERE id = %s AND tenant_id = %s",
            (document_id, tenant_id),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    return (
        str(row[0]),
        None if row[1] is None else str(row[1]),
        "" if row[2] is None else str(row[2]),
    )


def queue_reingestion(connection: Connection, document_id: UUID) -> bool:
    """Move a finished document back to pending for re-processing.

    Admits only ``ready`` and ``failed`` documents: a ``pending`` or leased
    ``processing`` document is already queued, so a re-ingest request for one
    is a conflict, not a re-queue.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE documents SET status = 'pending', status_changed_at = now() "
            "WHERE id = %s AND status IN ('ready', 'failed')",
            (document_id,),
        )
        return cursor.rowcount == 1


def claim_document(connection: Connection, document_id: UUID, tenant_id: UUID) -> bool:
    """Atomically claim a document for ingestion processing.

    Admits ``pending`` documents and ``processing`` documents whose 10-minute
    claim lease has expired (a crashed worker), so Pub/Sub at-least-once
    redeliveries cannot double-process a document. Returns whether this caller
    won the claim.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE documents
            SET status = 'processing', status_changed_at = now()
            WHERE id = %s AND tenant_id = %s
              AND (status = 'pending'
                   OR (status = 'processing'
                       AND status_changed_at < now() - interval '10 minutes'))
            """,
            (document_id, tenant_id),
        )
        return cursor.rowcount == 1


def update_document_status(connection: Connection, document_id: UUID, status: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE documents SET status = %s, status_changed_at = now() WHERE id = %s",
            (status, document_id),
        )


def find_document_by_hash(
    connection: Connection, document_hash: str, tenant_id: UUID | None = None
) -> tuple[UUID, int] | None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, version FROM documents WHERE content_hash = %s "
            "AND (%s IS NULL OR tenant_id = %s) ORDER BY version DESC LIMIT 1",
            (document_hash, tenant_id, tenant_id),
        )
        row = cursor.fetchone()
    return None if row is None else (UUID(str(row[0])), int(row[1]))


def find_latest_document_by_filename(
    connection: Connection, filename: str, tenant_id: UUID | None = None
) -> tuple[UUID, int] | None:
    with connection.cursor() as cursor:
        cursor.execute(
            (
                "SELECT id, version FROM documents "
                "WHERE source_filename = %s AND (%s IS NULL OR tenant_id = %s) "
                "ORDER BY version DESC LIMIT 1"
            ),
            (filename, tenant_id, tenant_id),
        )
        row = cursor.fetchone()
    return None if row is None else (UUID(str(row[0])), int(row[1]))


def mark_superseded(connection: Connection, old_document_id: UUID, new_document_id: UUID) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE documents SET superseded_by = %s WHERE id = %s",
            (new_document_id, old_document_id),
        )


def record_failed_ingestion(
    connection: Connection, filename: str, error_message: str, tenant_id: UUID
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO failed_ingestions (filename, error_message, tenant_id) "
            "VALUES (%s, %s, %s)",
            (filename, error_message, tenant_id),
        )


def list_failed_ingestions(
    connection: Connection, tenant_id: UUID, *, limit: int = 50, offset: int = 0
) -> list[tuple[UUID, str, str]]:
    if limit <= 0 or offset < 0:
        raise ValueError("limit must be positive and offset non-negative")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, filename, error_message FROM failed_ingestions "
            "WHERE tenant_id = %s ORDER BY attempted_at DESC LIMIT %s OFFSET %s",
            (tenant_id, limit, offset),
        )
        rows = cursor.fetchall()
    return [(UUID(str(row[0])), str(row[1]), str(row[2])) for row in rows]


def delete_document(
    connection: Connection, document_id: UUID, tenant_id: UUID
) -> tuple[bool, str | None]:
    """Delete a tenant-owned document, returning (found, storage_uri).

    ``found`` distinguishes a missing document from a document that exists but
    was ingested without cloud storage (NULL ``storage_uri``).
    """
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM documents WHERE id = %s AND tenant_id = %s RETURNING storage_uri",
                (document_id, tenant_id),
            )
            row = cursor.fetchone()
    if row is None:
        return False, None
    return True, None if row[0] is None else str(row[0])


def delete_tenant(connection: Connection, tenant_id: UUID) -> list[str]:
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT storage_uri FROM documents "
                "WHERE tenant_id = %s AND storage_uri IS NOT NULL",
                (tenant_id,),
            )
            storage_uris = [str(row[0]) for row in cursor.fetchall()]
            cursor.execute("DELETE FROM request_logs WHERE tenant_id = %s", (tenant_id,))
            cursor.execute("DELETE FROM documents WHERE tenant_id = %s", (tenant_id,))
            cursor.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))
    return storage_uris


def record_request_log(
    connection: Connection,
    *,
    request_id: UUID,
    tenant_id: UUID,
    query: str,
    retrieved_chunk_ids: Sequence[UUID],
    latency_ms: float,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_estimate: float = 0.0,
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO request_logs
                (request_id, tenant_id, query, retrieved_chunk_ids, latency_ms,
                 input_tokens, output_tokens, cost_estimate)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                request_id,
                tenant_id,
                query,
                list(retrieved_chunk_ids),
                latency_ms,
                input_tokens,
                output_tokens,
                cost_estimate,
            ),
        )


def tenant_usage(connection: Connection, tenant_id: UUID) -> tuple[int, int, float]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM documents WHERE tenant_id = %s", (tenant_id,))
        document_row = cursor.fetchone()
        if document_row is None:
            return 0, 0, 0.0
        document_count = int(document_row[0])
        cursor.execute("SELECT count(*) FROM request_logs WHERE tenant_id = %s", (tenant_id,))
        query_row = cursor.fetchone()
        query_count = int(query_row[0]) if query_row is not None else 0
        cursor.execute(
            "SELECT COALESCE(sum(cost_estimate), 0) FROM request_logs WHERE tenant_id = %s",
            (tenant_id,),
        )
        cost_row = cursor.fetchone()
        cost = float(cost_row[0]) if cost_row is not None else 0.0
    return document_count, query_count, cost


class UsageDayRow(NamedTuple):
    day: str
    queries: int
    input_tokens: int
    output_tokens: int
    cost_estimate: float


def tenant_usage_daily(
    connection: Connection, tenant_id: UUID, *, days: int = 30
) -> list[UsageDayRow]:
    """Per-day query/token/cost usage for the tenant dashboard (F5)."""
    if days <= 0:
        raise ValueError("days must be positive")
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT date_trunc('day', created_at), count(*),
                   COALESCE(sum(input_tokens), 0), COALESCE(sum(output_tokens), 0),
                   COALESCE(sum(cost_estimate), 0)
            FROM request_logs
            WHERE tenant_id = %s AND created_at >= now() - (%s * interval '1 day')
            GROUP BY 1
            ORDER BY 1
            """,
            (tenant_id, days),
        )
        rows = cursor.fetchall()
    return [
        UsageDayRow(
            day=str(row[0]),
            queries=int(row[1]),
            input_tokens=int(row[2]),
            output_tokens=int(row[3]),
            cost_estimate=float(row[4]),
        )
        for row in rows
    ]


def count_documents(connection: Connection, tenant_id: UUID) -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM documents WHERE tenant_id = %s", (tenant_id,))
        row = cursor.fetchone()
        return int(row[0]) if row is not None else 0
