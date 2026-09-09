-- Migration 029: Proactive Knowledge Drift & Contradiction Auditor (Feature 8)
-- Tracks cross-document factual contradictions, conflicting policy terms,
-- and superseded rules detected by background sentinels.

CREATE TABLE IF NOT EXISTS knowledge_conflicts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    doc_a_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    doc_b_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    conflict_category TEXT NOT NULL,
    description TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'MODERATE',
    status TEXT NOT NULL DEFAULT 'unresolved',
    resolved_by UUID REFERENCES users(id) ON DELETE SET NULL,
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS knowledge_conflicts_tenant_status_idx ON knowledge_conflicts (tenant_id, status);
CREATE INDEX IF NOT EXISTS knowledge_conflicts_doc_idx ON knowledge_conflicts (doc_a_id, doc_b_id);
