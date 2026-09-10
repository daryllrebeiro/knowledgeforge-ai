-- Migration 038: Auto-Clustering, Embeddable Widgets, and Mobile Push Channels (Phase 8 Items 5, 9, 10)

CREATE TABLE IF NOT EXISTS document_clusters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    document_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    suggested_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL DEFAULT 'suggested' CHECK (status IN ('suggested', 'confirmed', 'dismissed')),
    confirmed_collection_id UUID REFERENCES collections(id) ON DELETE SET NULL,
    reviewed_by UUID REFERENCES users(id) ON DELETE SET NULL,
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS document_clusters_tenant_idx ON document_clusters (tenant_id, status);

CREATE TABLE IF NOT EXISTS document_tags (
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, document_id, tag)
);

CREATE INDEX IF NOT EXISTS document_tags_doc_idx ON document_tags (document_id);

CREATE TABLE IF NOT EXISTS tenant_widgets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    collection_id UUID NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
    api_key_id UUID NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    allowed_origins TEXT[] NOT NULL DEFAULT '{}',
    primary_color TEXT NOT NULL DEFAULT '#2563eb',
    rate_limit_per_minute INT NOT NULL DEFAULT 30,
    daily_budget_tokens INT NOT NULL DEFAULT 50000,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS tenant_widgets_tenant_idx ON tenant_widgets (tenant_id);
CREATE INDEX IF NOT EXISTS tenant_widgets_api_key_idx ON tenant_widgets (api_key_id);

CREATE TABLE IF NOT EXISTS user_devices (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    platform TEXT NOT NULL CHECK (platform IN ('ios', 'android', 'web_push')),
    device_token TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT true,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_tenant_device UNIQUE (tenant_id, device_token)
);

CREATE INDEX IF NOT EXISTS user_devices_user_idx ON user_devices (user_id);
