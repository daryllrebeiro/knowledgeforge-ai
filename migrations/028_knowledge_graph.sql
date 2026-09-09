-- Migration 028: GraphRAG Multi-Hop Entity Engine (Feature 6)
-- Introduces emergent Knowledge Graph tables (entities and relationships)
-- with multi-tenant isolation and recursive traversal support.

CREATE TABLE IF NOT EXISTS graph_entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT graph_entities_unique_tenant_name_type UNIQUE (tenant_id, name, entity_type)
);

CREATE INDEX IF NOT EXISTS graph_entities_tenant_idx ON graph_entities (tenant_id, name);

CREATE TABLE IF NOT EXISTS graph_relationships (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    source_entity_id UUID NOT NULL REFERENCES graph_entities(id) ON DELETE CASCADE,
    target_entity_id UUID NOT NULL REFERENCES graph_entities(id) ON DELETE CASCADE,
    relation_type TEXT NOT NULL,
    source_doc_id UUID REFERENCES documents(id) ON DELETE SET NULL,
    confidence FLOAT NOT NULL DEFAULT 1.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS graph_relationships_source_idx ON graph_relationships (tenant_id, source_entity_id);
CREATE INDEX IF NOT EXISTS graph_relationships_target_idx ON graph_relationships (tenant_id, target_entity_id);
CREATE INDEX IF NOT EXISTS graph_relationships_relation_idx ON graph_relationships (relation_type);
