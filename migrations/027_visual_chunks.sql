-- Migration 027: Multimodal Visual Chunking & VQA (Feature 5)
-- Enables indexing visual crops (charts, architectural diagrams, flowcharts)
-- alongside text chunks for native Gemini Vision multimodal reasoning.

ALTER TABLE chunks
    ADD COLUMN IF NOT EXISTS modality VARCHAR(16) NOT NULL DEFAULT 'text',
    ADD COLUMN IF NOT EXISTS image_storage_uri TEXT;

CREATE INDEX IF NOT EXISTS chunks_modality_idx ON chunks (modality) WHERE modality != 'text';
