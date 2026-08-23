DROP INDEX IF EXISTS documents_content_hash_idx;
CREATE UNIQUE INDEX documents_tenant_content_hash_idx ON documents (tenant_id, content_hash);
