-- Migration 021: Subscription tiers, tenant billing columns, and Stripe webhook idempotency table

CREATE TABLE IF NOT EXISTS subscription_tiers (
    tier TEXT PRIMARY KEY,
    daily_token_budget BIGINT NOT NULL,
    daily_extraction_budget INT NOT NULL,
    unverified_token_budget BIGINT NOT NULL,
    unverified_extraction_budget INT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO subscription_tiers (tier, daily_token_budget, daily_extraction_budget, unverified_token_budget, unverified_extraction_budget)
VALUES
    ('free', 10000, 5, 1000, 1),
    ('pro', 1000000, 1000, 1000, 1),
    ('enterprise', 10000000, 10000, 1000, 1)
ON CONFLICT (tier) DO UPDATE SET
    daily_token_budget = EXCLUDED.daily_token_budget,
    daily_extraction_budget = EXCLUDED.daily_extraction_budget,
    unverified_token_budget = EXCLUDED.unverified_token_budget,
    unverified_extraction_budget = EXCLUDED.unverified_extraction_budget;

ALTER TABLE tenants
    ADD COLUMN IF NOT EXISTS tier TEXT NOT NULL DEFAULT 'free' REFERENCES subscription_tiers(tier),
    ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT,
    ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT,
    ADD COLUMN IF NOT EXISTS subscription_status TEXT NOT NULL DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS current_period_end TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS tenants_stripe_customer_idx ON tenants (stripe_customer_id);
CREATE INDEX IF NOT EXISTS tenants_stripe_subscription_idx ON tenants (stripe_subscription_id);

CREATE TABLE IF NOT EXISTS stripe_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id TEXT UNIQUE NOT NULL,
    event_type TEXT NOT NULL,
    tenant_id UUID REFERENCES tenants(id) ON DELETE SET NULL,
    processed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS stripe_events_event_id_idx ON stripe_events (event_id);
