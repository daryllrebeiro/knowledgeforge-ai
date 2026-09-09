-- Migration 026: Dynamic Zero-Code Schema Studio (Feature 3)
-- Allows tenants to define and manage dynamic extraction schemas at runtime
-- without code deploys or fixed Pydantic schemas.

CREATE TABLE IF NOT EXISTS tenant_extraction_schemas (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    schema_name TEXT NOT NULL,
    schema_version INT NOT NULL DEFAULT 1,
    description TEXT NOT NULL DEFAULT '',
    json_schema JSONB NOT NULL,
    field_descriptions JSONB NOT NULL DEFAULT '{}'::jsonb,
    active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT tenant_extraction_schemas_unique_name_ver UNIQUE (tenant_id, schema_name, schema_version)
);

CREATE INDEX IF NOT EXISTS tenant_extraction_schemas_tenant_idx ON tenant_extraction_schemas (tenant_id, active);
