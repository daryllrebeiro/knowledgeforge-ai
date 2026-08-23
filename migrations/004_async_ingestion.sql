ALTER TABLE documents ADD COLUMN status TEXT NOT NULL DEFAULT 'ready';
ALTER TABLE documents ADD COLUMN storage_uri TEXT;
CREATE INDEX documents_status_idx ON documents (status);
