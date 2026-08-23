CREATE TABLE tenants (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  email TEXT UNIQUE NOT NULL,
  hashed_password TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE documents ADD COLUMN tenant_id UUID REFERENCES tenants(id);
DO $$
DECLARE default_tenant UUID;
BEGIN
  INSERT INTO tenants (name) VALUES ('Default tenant') RETURNING id INTO default_tenant;
  UPDATE documents SET tenant_id = default_tenant WHERE tenant_id IS NULL;
END $$;
ALTER TABLE documents ALTER COLUMN tenant_id SET NOT NULL;
CREATE INDEX documents_tenant_id_idx ON documents (tenant_id);
