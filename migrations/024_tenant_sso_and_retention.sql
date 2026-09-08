-- Migration 024: Enterprise OIDC SSO Configuration, Data Retention & Residency (F7, F8)

-- 1. Tenant SSO Configurations (Enterprise tier)
CREATE TABLE IF NOT EXISTS tenant_sso_configs (
    tenant_id UUID PRIMARY KEY REFERENCES tenants(id) ON DELETE CASCADE,
    enabled BOOLEAN NOT NULL DEFAULT false,
    issuer_url TEXT NOT NULL,
    client_id TEXT NOT NULL,
    client_secret TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2. Tenant Configurable Retention & Data Residency Policy
ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS retention_days INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS data_residency TEXT NOT NULL DEFAULT 'us';
