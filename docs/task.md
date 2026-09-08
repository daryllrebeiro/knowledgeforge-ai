# KnowledgeForge AI — Phase 6 Task Tracker: Bridge, Verify, Then Extend

Status Taxonomy:
- **Implemented**: Code and configuration written and committed; passes unit tests against mocks.
- **Verified**: Exercised against a real external system or real infrastructure, with specific evidence cited.
- **Done**: Verified, plus survived a targeted adversarial audit pass with independent reviewer sign-off `(Signed-off-by: <reviewer>)`.
- **Gated**: External dependency blocked (credentials, live cloud billing, or external API quota).
- **Retracted**: Unsubstantiated claims removed following audit findings.

---

## Item 1 — Close the Unverified-Account Purge Call-Site Gap (Phase 5 Item 7 Follow-Up)
- [Verified] Periodic purge entrypoint implemented at `src/knowledgeforge/security/purge_job.py` with CLI interface (`--max-age-days`, `--purge-documents`, `--dry-run`)
- [Verified] Unit tests in `tests/unit/test_purge_job.py` verifying dry-run safety and actual deletion of stale unverified accounts
- [Verified] Integration tests against real PostgreSQL in `tests/integration/test_gdpr_postgres.py`
- [Verified] Phase 5 Items 1 and 9 self-certified [Done] statuses demoted to [Verified] in compliance with Ground Rule 5

## Item 2 — Real Stripe Integration via stripe-mock (Bridge the Verification Gap)
- [Verified] `stripe-mock` service (`stripe/stripe-mock:v0.190.0`) added on port 12111 in `docker-compose.full.yml`
- [Verified] `stripe_api_base` added to settings in `src/knowledgeforge/config.py` and `src/knowledgeforge/billing/stripe_client.py`
- [Verified] Integration test suite `tests/integration/test_stripe_mock.py` and mock client suite `tests/unit/test_stripe_client_mock_server.py` exercising checkout session, portal session, and webhook lifecycle against `stripe-mock`
- [Gated] Live test-mode Stripe API verification (blocked on real `sk_test_...` key)

## Item 3 — Ready-to-Fire Verification Harnesses for Gated Items (Items 3, 6, 8)
- [Verified] Infrastructure verification harness `scripts/verify_infra_live.py` with `--dry-run` validation and Cloud Run / Cloud SQL checks
- [Verified] Alert policy verification harness `scripts/verify_alerts_live.py` with `--dry-run` validation and Cloud Monitoring checks
- [Verified] Disaster recovery verification harness `scripts/verify_dr_live.py` with `--dry-run` validation and PITR backup checksum checks
- [Verified] Unit test suite `tests/unit/test_live_harnesses_dry_run.py` verifying `--dry-run` exit codes and output contracts
- [Gated] Live infrastructure provisioning and execution against live GCP project (blocked on deployment approval)

## Item 4 — Resumable Gemini Evaluation Harness with Checkpoints and Cost Guardrails (Item 2 Bridge)
- [Verified] Resumable checkpoint runner with `CheckpointTracker` in `evaluation/run_phase12_eval.py`
- [Verified] Spend cap guardrail (`--max-spend-usd`) and graceful 429 quota handling with checkpoint preservation
- [Verified] Unit tests in `tests/unit/test_resumable_eval.py` testing resume logic, spend cap trip, and 429 backoff
- [Verified] Deterministic local evaluation smoke test (`python -m evaluation.run_phase12_eval --local`) producing `docs/phase12-eval-checkpoint.json`
- [Gated] Live Gemini evaluation across all 3 chunking profiles (blocked on live API quota)

## Item 5 — Strengthen the Mechanical Integrity Tooling (Item 9 Follow-Up)
- [Verified] Non-phantom file existence verification added to `scripts/verify_decisions.py`
- [Verified] Ground Rule 5 sign-off validator (`validate_task_integrity()`) added to `scripts/generate_task_summary.py`
- [Verified] Unit test suites `tests/unit/test_decisions_integrity.py` and `tests/unit/test_task_summary.py` passing with 100% coverage

## Item 6 — Self-Service Tenant Dashboard & Usage API (Feature Track F5)
- [Implemented] `GET /tenant/usage` and `GET /tenant/dashboard` endpoints added to `src/knowledgeforge/api.py` strictly scoped to caller's `tenant_id`
- [Implemented] Daily trend metrics, token and extraction budget tracking, and tenant metadata reporting
- [Implemented] Unit test suite `tests/unit/test_tenant_dashboard.py` verifying tenant scoping, data accuracy, and role permissions

## Item 7 — Outbound Webhooks with SSRF-Safe Delivery (Feature Track F6)
- [Implemented] Database migration `migrations/023_tenant_webhooks.sql` for webhooks and delivery tracking
- [Implemented] SSRF validation engine `src/knowledgeforge/security/ssrf.py` blocking loopback, link-local, private RFC 1918, and cloud metadata (`169.254.169.254`)
- [Implemented] Webhook dispatcher `src/knowledgeforge/security/webhooks.py` with timing-safe HMAC SHA-256 signatures, exponential backoff, and dead-letter queue
- [Implemented] Webhook management endpoints `POST /tenant/webhooks`, `GET /tenant/webhooks`, `DELETE /tenant/webhooks/{id}`
- [Implemented] Unit test suite `tests/unit/test_outbound_webhooks.py` covering SSRF rejection, signature verification, and delivery retries

## Item 8 — Enterprise OIDC SSO Integration (Feature Track F7)
- [Implemented] Database migration `migrations/024_tenant_sso_and_retention.sql` for tenant SSO configurations
- [Implemented] OIDC SSO integration `src/knowledgeforge/security/sso.py` gated to Enterprise tier with CSRF state tokens, JIT provisioning, and standard JWT minting
- [Implemented] SSO endpoints `PUT /tenant/sso/config`, `GET /tenant/sso/config`, `POST /auth/sso/oidc/authorize`, `POST /auth/sso/oidc/callback`
- [Implemented] Unit test suite `tests/unit/test_sso.py` covering tier enforcement, CSRF state verification, JIT user provisioning, and claim validation

## Item 9 — Configurable Retention and Data Residency Settings (Feature Track F8)
- [Implemented] Tenant retention and data residency columns (`retention_days`, `data_residency`) in `migrations/024_tenant_sso_and_retention.sql`
- [Implemented] Endpoints `GET /tenant/settings` and `PUT /tenant/settings` in `src/knowledgeforge/api.py` with owner-only mutation and residency validation
- [Implemented] Document retention purge job `purge_expired_documents` in `src/knowledgeforge/security/purge_job.py`
- [Implemented] Unit test suite `tests/unit/test_retention_settings.py` verifying retention enforcement, residency options, and purge execution

## Item 10 — Task Tracking, Decisions Log, and Capstone Verification
- [Verified] Ground Rule 5 sign-off enforcement verified mechanically by `scripts/generate_task_summary.py`
- [Verified] Decisions citations verified mechanically without phantom files by `scripts/verify_decisions.py`
- [Verified] OpenAPI specification synchronized and verified with zero drift (`docs/openapi.json`)
- [Verified] Complete test suite passing with test coverage exceeding the 58.00% floor
