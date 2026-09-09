-- Migration 025: Spatial Bounding-Box Grounding (Feature 1)
-- Stores normalized sub-page coordinates [x0, y0, x1, y1] for each chunk to support
-- exact interactive visual highlights and visual grounding in PDF documents.

ALTER TABLE chunks
    ADD COLUMN IF NOT EXISTS bounding_boxes JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE INDEX IF NOT EXISTS chunks_bounding_boxes_idx ON chunks USING GIN (bounding_boxes);
