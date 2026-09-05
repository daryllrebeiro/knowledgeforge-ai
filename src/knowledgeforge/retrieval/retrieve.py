from collections.abc import Sequence
from uuid import UUID

from pgvector.psycopg import register_vector
from psycopg import Connection

from knowledgeforge.ingestion.chunk import TextChunk


def retrieve_chunks(
    connection: Connection,
    query_embedding: Sequence[float],
    *,
    tenant_id: UUID,
    question: str = "",
    limit: int = 5,
    doc_type: str | None = None,
    document_id: UUID | None = None,
    document_ids: Sequence[UUID] | None = None,
    hybrid: bool = False,
    hybrid_lexical_weight: float = 0.15,
) -> list[tuple[UUID, UUID, TextChunk]]:
    """Return ``(chunk_id, document_id, chunk)`` nearest neighbours for a tenant.

    Tenant scoping is mandatory: there is deliberately no unfiltered branch, so
    a caller cannot accidentally search every tenant's chunks. ``document_id``
    scopes to one document; ``document_ids`` (the structured_filters pre-step)
    scopes to a matched set — both are tenant-and-ed on top of the mandatory
    tenant filter. When ``hybrid`` is set, cosine similarity is combined with a
    weighted tsvector lexical rank (migration 009), which helps exact-match
    questions (IDs, error codes).
    """
    if limit <= 0:
        raise ValueError("limit must be positive")

    register_vector(connection)
    # pgvector's psycopg adapters register dumpers for Vector/numpy only, so a
    # plain list would arrive as double precision[] and fail the <=> operator.
    from pgvector import Vector

    embedding = Vector(query_embedding)
    # Optional filters are appended as explicit clauses rather than the
    # "(%s IS NULL OR col = %s)" idiom: a NULL parameter in that pattern is
    # sent as an untyped value some server/psycopg combinations reject, and
    # present-only clauses give the planner exact filters anyway.
    clauses = ["d.tenant_id = %s"]
    params: list[object] = [tenant_id]
    if doc_type is not None:
        clauses.append("d.doc_type = %s")
        params.append(doc_type)
    if document_id is not None:
        clauses.append("c.document_id = %s")
        params.append(document_id)
    if document_ids is not None:
        if not document_ids:
            # An explicitly empty match set must return nothing, not
            # everything — an empty IN-style filter is always false.
            return []
        clauses.append("c.document_id = ANY(%s)")
        params.append(list(document_ids))
    filters = "WHERE " + " AND ".join(clauses)
    filter_params = tuple(params)
    if hybrid:
        if not question:
            raise ValueError("hybrid retrieval requires the question text")
        query = f"""
            SELECT c.id, c.document_id, c.page, c.section, c.chunk_text
            FROM chunks AS c
            JOIN documents AS d ON d.id = c.document_id
            {filters}
            ORDER BY (1 - (c.embedding <=> %s))
                     + %s * ts_rank(c.lexical, plainto_tsquery('english', %s)) DESC
            LIMIT %s
        """
        final_params = (*filter_params, embedding, hybrid_lexical_weight, question, limit)
    else:
        query = f"""
            SELECT c.id, c.document_id, c.page, c.section, c.chunk_text
            FROM chunks AS c
            JOIN documents AS d ON d.id = c.document_id
            {filters}
            ORDER BY c.embedding <=> %s
            LIMIT %s
        """
        final_params = (*filter_params, embedding, limit)
    with connection.cursor() as cursor:
        cursor.execute(query, final_params)
        rows = cursor.fetchall()
    return [
        (chunk_id, document_id, TextChunk(text=chunk_text, page=page, section=section))
        for chunk_id, document_id, page, section, chunk_text in rows
    ]
