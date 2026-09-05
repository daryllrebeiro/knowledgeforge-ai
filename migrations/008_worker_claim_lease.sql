ALTER TABLE documents ADD COLUMN status_changed_at TIMESTAMPTZ NOT NULL DEFAULT now();
UPDATE documents SET status_changed_at = created_at;
