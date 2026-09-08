-- Invitations: token-based, expiring, tied to tenant + role (F4)
CREATE TABLE invitations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('owner', 'member')),
    token_hash TEXT NOT NULL,  -- SHA-256 of the opaque token
    email TEXT,  -- optional: pre-assign to email
    expires_at TIMESTAMPTZ NOT NULL,
    accepted_at TIMESTAMPTZ,
    accepted_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX invitations_tenant_idx ON invitations (tenant_id);
CREATE INDEX invitations_token_hash_idx ON invitations (token_hash);
CREATE INDEX invitations_expires_at_idx ON invitations (expires_at);