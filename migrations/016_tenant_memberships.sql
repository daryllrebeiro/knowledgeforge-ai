-- Multi-user tenants: membership table with owner/member roles (F4)
CREATE TABLE tenant_memberships (
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('owner', 'member')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, user_id)
);

CREATE INDEX tenant_memberships_user_idx ON tenant_memberships (user_id);

-- Migrate existing single-tenant users to membership table with 'owner' role
INSERT INTO tenant_memberships (tenant_id, user_id, role)
SELECT t.id, u.id, 'owner'
FROM tenants t
JOIN users u ON u.tenant_id = t.id
ON CONFLICT (tenant_id, user_id) DO NOTHING;

-- The users.tenant_id column is kept for backward compatibility during transition
-- but new code should use tenant_memberships for multi-tenant support