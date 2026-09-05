"""Embedding cache (F2): identical chunk text is never re-embedded.

Vectors are keyed by the SHA-256 of the chunk text, so re-uploading the same
content — a new version of a document, or the same content in another tenant —
reuses stored vectors instead of paying embedding tokens again. Token
telemetry counts only the tokens actually spent on cache misses.
"""

from collections.abc import Sequence

from google import genai
from pgvector.psycopg import register_vector
from psycopg import Connection

from knowledgeforge.ingestion.dedup import content_hash
from knowledgeforge.ingestion.embed import EmbeddingResult, embed_texts


def _parse_vector(text: str) -> list[float]:
    return [float(value) for value in text.strip()[1:-1].split(",")]


def embed_texts_cached(
    connection: Connection,
    client: genai.Client,
    texts: Sequence[str],
    *,
    model: str,
) -> EmbeddingResult:
    """Embed ``texts`` through the cache: served from it on hit, stored on miss."""
    # The model is part of the key so switching embedding models cannot serve
    # vectors from the previous model.
    hashes = [content_hash(f"{model}:{text}".encode()) for text in texts]
    vectors: dict[str, list[float]] = {}
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT content_hash, embedding::text FROM embedding_cache "
            "WHERE content_hash = ANY(%s)",
            (hashes,),
        )
        for row in cursor.fetchall():
            vectors[str(row[0])] = _parse_vector(str(row[1]))
    missing = [index for index, digest in enumerate(hashes) if digest not in vectors]
    input_tokens = 0
    if missing:
        result = embed_texts(client, [texts[index] for index in missing], model=model)
        input_tokens = result.input_tokens
        # Per-chunk token counts are not reported by the batch API; storing the
        # batch average keeps the cache's bookkeeping useful without inventing
        # precision.
        per_chunk_tokens = round(result.input_tokens / len(missing))
        register_vector(connection)
        with connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO embedding_cache (content_hash, embedding, input_tokens) "
                "VALUES (%s, %s, %s) ON CONFLICT (content_hash) DO NOTHING",
                [
                    (hashes[index], list(result.vectors[hit]), per_chunk_tokens)
                    for hit, index in enumerate(missing)
                ],
            )
        for hit, index in enumerate(missing):
            vectors[hashes[index]] = list(result.vectors[hit])
    return EmbeddingResult(
        vectors=[vectors[digest] for digest in hashes], input_tokens=input_tokens
    )
