ALTER TABLE failed_ingestions ADD COLUMN tenant_id UUID REFERENCES tenants(id) ON DELETE CASCADE;
UPDATE failed_ingestions
SET tenant_id = (SELECT id FROM tenants ORDER BY created_at LIMIT 1)
WHERE tenant_id IS NULL;
ALTER TABLE failed_ingestions ALTER COLUMN tenant_id SET NOT NULL;
CREATE INDEX failed_ingestions_tenant_idx ON failed_ingestions (tenant_id, attempted_at DESC);
