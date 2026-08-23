ALTER TABLE documents ADD COLUMN content_hash TEXT;
UPDATE documents
SET content_hash = encode(digest(source_filename || id::text, 'sha256'), 'hex')
WHERE content_hash IS NULL;
ALTER TABLE documents ALTER COLUMN content_hash SET NOT NULL;
ALTER TABLE documents ALTER COLUMN content_hash SET DEFAULT '';
ALTER TABLE documents ADD COLUMN version INT NOT NULL DEFAULT 1;
ALTER TABLE documents ADD COLUMN superseded_by UUID REFERENCES documents(id);
CREATE UNIQUE INDEX documents_content_hash_idx ON documents (content_hash);

CREATE TABLE failed_ingestions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  filename TEXT NOT NULL,
  error_message TEXT NOT NULL,
  attempted_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
