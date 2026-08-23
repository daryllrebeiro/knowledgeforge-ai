from collections.abc import Sequence
from uuid import UUID

from pgvector.psycopg import register_vector
from psycopg import Connection

from knowledgeforge.ingestion.chunk import TextChunk


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
            cursor.execute("UPDATE documents SET status = 'ready' WHERE id = %s", (document_id,))


def get_document_status(connection: Connection, document_id: UUID, tenant_id: UUID) -> str | None:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT status FROM documents WHERE id = %s AND tenant_id = %s",
            (document_id, tenant_id),
        )
        row = cursor.fetchone()
    return None if row is None else str(row[0])


def update_document_status(connection: Connection, document_id: UUID, status: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute("UPDATE documents SET status = %s WHERE id = %s", (status, document_id))


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


def list_failed_ingestions(connection: Connection, tenant_id: UUID) -> list[tuple[UUID, str, str]]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id, filename, error_message FROM failed_ingestions "
            "WHERE tenant_id = %s ORDER BY attempted_at DESC",
            (tenant_id,),
        )
        rows = cursor.fetchall()
    return [(UUID(str(row[0])), str(row[1]), str(row[2])) for row in rows]


def delete_document(connection: Connection, document_id: UUID, tenant_id: UUID) -> str | None:
    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM documents WHERE id = %s AND tenant_id = %s RETURNING storage_uri",
                (document_id, tenant_id),
            )
            row = cursor.fetchone()
    return None if row is None else row[0]


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
) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO request_logs
                (request_id, tenant_id, query, retrieved_chunk_ids, latency_ms)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (request_id, tenant_id, query, list(retrieved_chunk_ids), latency_ms),
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


def count_documents(connection: Connection, tenant_id: UUID) -> int:
    with connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM documents WHERE tenant_id = %s", (tenant_id,))
        row = cursor.fetchone()
        return int(row[0]) if row is not None else 0
