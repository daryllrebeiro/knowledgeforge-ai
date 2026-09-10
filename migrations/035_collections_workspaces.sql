-- Migration 035: Collections and Sub-Tenant Workspaces (Phase 8 Item 1)
-- Introduces sub-tenant isolation boundary with collections, memberships, and document bindings.

CREATE TABLE IF NOT EXISTS collections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    is_private BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS collections_tenant_idx ON collections (tenant_id);

CREATE TABLE IF NOT EXISTS collection_documents (
    collection_id UUID NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (collection_id, document_id)
);

CREATE INDEX IF NOT EXISTS collection_documents_doc_idx ON collection_documents (document_id);
CREATE INDEX IF NOT EXISTS collection_documents_tenant_idx ON collection_documents (tenant_id);

CREATE TABLE IF NOT EXISTS collection_memberships (
    collection_id UUID NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('viewer', 'editor', 'admin')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (collection_id, user_id)
);

CREATE INDEX IF NOT EXISTS collection_memberships_user_idx ON collection_memberships (user_id);
CREATE INDEX IF NOT EXISTS collection_memberships_tenant_idx ON collection_memberships (tenant_id);
