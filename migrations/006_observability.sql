CREATE TABLE request_logs (
  request_id UUID PRIMARY KEY,
  tenant_id UUID REFERENCES tenants(id),
  query TEXT,
  retrieved_chunk_ids UUID[] NOT NULL DEFAULT '{}',
  latency_ms DOUBLE PRECISION NOT NULL,
  input_tokens INT,
  output_tokens INT,
  cost_estimate NUMERIC(12, 8),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX request_logs_tenant_created_idx ON request_logs (tenant_id, created_at DESC);
