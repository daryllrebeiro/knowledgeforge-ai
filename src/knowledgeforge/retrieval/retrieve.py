from collections.abc import Sequence
from uuid import UUID

from pgvector.psycopg import register_vector
from psycopg import Connection

from knowledgeforge.ingestion.chunk import TextChunk


def retrieve_chunks(
    connection: Connection,
    query_embedding: Sequence[float],
    *,
    limit: int = 5,
    doc_type: str | None = None,
    tenant_id: UUID | None = None,
) -> list[tuple[UUID, TextChunk]]:
    """Return the nearest chunks using cosine distance in pgvector."""
    if limit <= 0:
        raise ValueError("limit must be positive")

    register_vector(connection)
    with connection.cursor() as cursor:
        if doc_type is None and tenant_id is None:
            cursor.execute(
                """
                SELECT document_id, page, section, chunk_text
                FROM chunks
                ORDER BY embedding <=> %s
                LIMIT %s
                """,
                (list(query_embedding), limit),
            )
        else:
            cursor.execute(
                """
                SELECT c.document_id, c.page, c.section, c.chunk_text
                FROM chunks AS c
                JOIN documents AS d ON d.id = c.document_id
                WHERE (%s IS NULL OR d.doc_type = %s)
                  AND (%s IS NULL OR d.tenant_id = %s)
                ORDER BY c.embedding <=> %s
                LIMIT %s
                """,
                (doc_type, doc_type, tenant_id, tenant_id, list(query_embedding), limit),
            )
        rows = cursor.fetchall()
    return [
        (document_id, TextChunk(text=chunk_text, page=page, section=section))
        for document_id, page, section, chunk_text in rows
    ]
