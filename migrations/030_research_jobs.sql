-- Migration 030: Agentic Deep Research Planner (Feature 9)
-- Stores long-running recursive investigation jobs, goal breakdowns, execution steps, and compiled research dossiers.

CREATE TABLE IF NOT EXISTS research_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    query TEXT NOT NULL,
    plan JSONB NOT NULL DEFAULT '[]'::jsonb,
    steps JSONB NOT NULL DEFAULT '[]'::jsonb,
    sources JSONB NOT NULL DEFAULT '[]'::jsonb,
    final_report TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_research_jobs_tenant_status ON research_jobs (tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_research_jobs_created_at ON research_jobs (created_at DESC);
