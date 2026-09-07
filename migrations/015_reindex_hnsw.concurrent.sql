-- Reindex for production: replace empty ivfflat with HNSW (H6)
-- The original ivfflat index (migration 001) was built on an empty table.
-- HNSW is preferred: no build-time tuning, better recall/latency trade-off,
-- and works correctly regardless of when data is loaded.
-- CREATE INDEX CONCURRENTLY cannot run inside a transaction block;
-- this migration must be run with autocommit (scripts/apply_migrations.py handles this).
-- Create new index first under a temporary name, then drop old, then rename.
CREATE INDEX CONCURRENTLY chunks_embedding_idx_hnsw
  ON chunks USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);
-- Verify the new index built successfully
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_index
        WHERE indexrelid = 'chunks_embedding_idx_hnsw'::regclass
          AND indisvalid
    ) THEN
        RAISE EXCEPTION 'HNSW index build did not complete successfully';
    END IF;
END $$;
-- Drop old ivfflat index concurrently (brief lock, but non-blocking for reads)
DROP INDEX CONCURRENTLY IF EXISTS chunks_embedding_idx;
-- Rename new index into the canonical name used by application queries
ALTER INDEX chunks_embedding_idx_hnsw RENAME TO chunks_embedding_idx;