-- Embedding cache (F2): identical chunk text is never re-embedded. Keyed by the
-- SHA-256 of the chunk text, so re-uploading the same content (across versions
-- or tenants) reuses stored vectors and stops paying embedding tokens.
CREATE TABLE embedding_cache (
    content_hash TEXT PRIMARY KEY,
    embedding VECTOR(768) NOT NULL,
    input_tokens INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
