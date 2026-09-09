-- Migration 032: Chunk Character Offsets (Phase 7 Wave 1 Item C)
-- Stores character offsets within document/page text for precise source passage highlighting.

ALTER TABLE chunks
    ADD COLUMN IF NOT EXISTS start_char INT,
    ADD COLUMN IF NOT EXISTS end_char INT;

CREATE INDEX IF NOT EXISTS chunks_offsets_idx ON chunks (document_id, start_char, end_char);
