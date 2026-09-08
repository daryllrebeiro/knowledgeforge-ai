-- Migration 022: Extraction corrections history and review audit trail

ALTER TABLE document_extractions
    ADD COLUMN IF NOT EXISTS extraction_history JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS reviewed_by UUID REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS document_extractions_reviewed_at_idx ON document_extractions (reviewed_at);
