-- Migration 037: Multi-Step Human Approval Workflows with Database Invariant Backstops (Phase 8 Item 7)
-- Structural database backstops enforce that:
-- 1. No document can be marked final while approval steps are outstanding.
-- 2. No approval step can be double-processed (UNIQUE constraint).
-- 3. An approval instance cannot be marked approved before reaching total_steps.

CREATE TABLE IF NOT EXISTS approval_chains (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    steps JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS approval_chains_tenant_idx ON approval_chains (tenant_id);

CREATE TABLE IF NOT EXISTS approval_instances (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chain_id UUID NOT NULL REFERENCES approval_chains(id) ON DELETE CASCADE,
    current_step_index INT NOT NULL DEFAULT 0,
    total_steps INT NOT NULL,
    status TEXT NOT NULL DEFAULT 'in_progress' CHECK (status IN ('in_progress', 'approved', 'rejected')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS approval_instances_doc_idx ON approval_instances (document_id);
CREATE INDEX IF NOT EXISTS approval_instances_tenant_status_idx ON approval_instances (tenant_id, status);

CREATE TABLE IF NOT EXISTS approval_actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    instance_id UUID NOT NULL REFERENCES approval_instances(id) ON DELETE CASCADE,
    step_index INT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('approve', 'reject')),
    actor_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    comments TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_approval_instance_step UNIQUE (instance_id, step_index)
);

CREATE INDEX IF NOT EXISTS approval_actions_instance_idx ON approval_actions (instance_id);

-- Structural DB Trigger: Enforce approval instance completion before setting status = 'approved'
CREATE OR REPLACE FUNCTION enforce_approval_completion_invariant()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.status = 'approved' THEN
        IF NEW.current_step_index < NEW.total_steps THEN
            RAISE EXCEPTION 'Cannot approve instance: outstanding steps remain';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_enforce_approval_completion ON approval_instances;
CREATE TRIGGER trg_enforce_approval_completion
BEFORE UPDATE ON approval_instances
FOR EACH ROW
EXECUTE FUNCTION enforce_approval_completion_invariant();

-- Structural DB Trigger: Prevent marking a document final if required approval steps remain outstanding
CREATE OR REPLACE FUNCTION enforce_document_finalization_invariant()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.status = 'final' AND (OLD.status IS DISTINCT FROM 'final') THEN
        -- Document must have at least one approved approval instance with all steps completed,
        -- and zero incomplete/pending approval instances.
        IF NOT EXISTS (
            SELECT 1 FROM approval_instances ai
            WHERE ai.document_id = NEW.id
              AND ai.status = 'approved'
              AND ai.current_step_index >= ai.total_steps
        ) OR EXISTS (
            SELECT 1 FROM approval_instances ai
            WHERE ai.document_id = NEW.id
              AND ai.status = 'in_progress'
        ) THEN
            RAISE EXCEPTION 'Cannot mark document final: outstanding approval steps remain';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_enforce_document_finalization ON documents;
CREATE TRIGGER trg_enforce_document_finalization
BEFORE UPDATE ON documents
FOR EACH ROW
EXECUTE FUNCTION enforce_document_finalization_invariant();
