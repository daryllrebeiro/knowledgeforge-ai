-- Phase 2.5: structured extraction pipeline.
--
-- document_extractions stores validated, typed fields (JSONB keeps the storage
-- schema-agnostic so a second document type later needs no migration). The
-- unique key is tenant-scoped, matching the documents (tenant_id, content_hash)
-- uniqueness rule: identical bytes in two tenants are two extractions.
CREATE TABLE document_extractions (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    document_id      UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    content_hash     TEXT NOT NULL,
    schema_type      TEXT NOT NULL,
    schema_version   INTEGER NOT NULL DEFAULT 1,
    model            TEXT NOT NULL,
    fields           JSONB NOT NULL,
    field_confidence JSONB NOT NULL,
    overall_confidence NUMERIC(4,3) NOT NULL,
    needs_review     BOOLEAN NOT NULL DEFAULT FALSE,
    input_tokens     INTEGER NOT NULL DEFAULT 0,
    output_tokens    INTEGER NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (tenant_id, content_hash, schema_type, schema_version, model)
);

CREATE INDEX idx_extractions_tenant ON document_extractions (tenant_id, created_at DESC);
CREATE INDEX idx_extractions_document ON document_extractions (document_id);
-- Review queue: tenant-scoped list of low-confidence extractions.
CREATE INDEX idx_extractions_review_queue
    ON document_extractions (tenant_id, created_at DESC) WHERE needs_review;
-- Field-level filtering (e.g. fields->>'vendor_name' = 'Acme').
CREATE INDEX idx_extractions_fields ON document_extractions USING GIN (fields);

-- Failed extractions mirror failed_ingestions: the raw model output and error
-- are recorded instead of silently retrying; the DLQ owns retry exhaustion.
CREATE TABLE failed_extractions (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    document_id   UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    schema_type   TEXT NOT NULL,
    raw_output    TEXT,
    error         TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 1,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_failed_extractions_tenant ON failed_extractions (tenant_id, created_at DESC);

-- Extraction job lifecycle. The ingestion-ready path and explicit reprocess
-- requests each create one job; the partial unique index allows at most one
-- active (queued/processing) job per document, which is the reprocess 409.
CREATE TABLE extraction_jobs (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    document_id    UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    content_hash   TEXT NOT NULL,
    schema_type    TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    model          TEXT NOT NULL,
    reason         TEXT NOT NULL DEFAULT 'ready',
    status         TEXT NOT NULL DEFAULT 'queued',
    detail         TEXT,
    attempt_count  INTEGER NOT NULL DEFAULT 0,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX extraction_jobs_one_active_per_document
    ON extraction_jobs (document_id) WHERE status IN ('queued', 'processing');
CREATE INDEX extraction_jobs_tenant ON extraction_jobs (tenant_id, created_at DESC);
CREATE INDEX extraction_jobs_status ON extraction_jobs (status);

-- Transactional outbox: the ingestion transaction writes the ready state and
-- an unsent event together; a bounded dispatcher publishes and marks sent.
-- PostgreSQL and Pub/Sub are never treated as one atomic transaction.
CREATE TABLE extraction_outbox (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id        UUID NOT NULL UNIQUE REFERENCES extraction_jobs(id) ON DELETE CASCADE,
    tenant_id     UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    payload       JSONB NOT NULL,
    sent_at       TIMESTAMPTZ,
    claimed_until TIMESTAMPTZ,
    attempts      INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX extraction_outbox_unsent
    ON extraction_outbox (created_at) WHERE sent_at IS NULL;

-- Classification happens once, before schema selection, and must be queryable
-- even for documents that never get an extraction row.
ALTER TABLE documents
    ADD COLUMN detected_doc_type TEXT,
    ADD COLUMN doc_type_confidence NUMERIC(4,3);
