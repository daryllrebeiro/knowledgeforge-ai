-- Migration 031: Zero-Knowledge Privacy Vault (Feature 10)
-- Stores tenant-scoped pseudonym surrogate mappings for sensitive PII/PHI redaction.

CREATE TABLE IF NOT EXISTS pseudonym_vault (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    surrogate_token TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    encrypted_value TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_pseudonym_tenant_surrogate UNIQUE (tenant_id, surrogate_token)
);

CREATE INDEX IF NOT EXISTS idx_pseudonym_vault_tenant ON pseudonym_vault (tenant_id);
CREATE INDEX IF NOT EXISTS idx_pseudonym_vault_created ON pseudonym_vault (created_at DESC);
