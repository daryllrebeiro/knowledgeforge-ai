-- Hybrid search support (roadmap R3.5): a Postgres-native lexical column so
-- exact-match questions (IDs, error codes) can be scored alongside cosine
-- distance. Off by default; enabled with HYBRID_SEARCH_ENABLED=true.
ALTER TABLE chunks ADD COLUMN lexical tsvector
  GENERATED ALWAYS AS (to_tsvector('english', chunk_text)) STORED;
CREATE INDEX chunks_lexical_idx ON chunks USING GIN (lexical);
