# KnowledgeForge AI — Phase 4 Task Tracker

Status Taxonomy:
- **Implemented**: Code and configuration written and committed.
- **Verified**: Validated via automated tests or empirical benchmarks.
- **Done**: Fully completed, audited, and closed.
- **Gated**: Blocked on external credentials, live cloud provisioning, or empirical live measurements.

---

## Item 1 — Close the Round 4 Remediation Loop
- [Done] Verify advisory-lock DB trigger (migration 019) is exercised by tests
- [Done] Verify XXE guard extends to `.docx` via `defusedxml`
- [Done] Verify budget `release_reservation` + TTL backstop logic
- [Done] Verify `registration_rate_limit_per_hour` is wired to `/auth/register`
- [Done] Verify `ensure_owner_remaining` call-site correctness
- [Done] Cross-check all regression hypotheses in `docs/regression-hypotheses.md`

## Item 2 — Real Gemini-Embedding Evaluation
- [Implemented] Evaluation harness committed (`evaluation/run_phase12_eval.py`)
- [Gated] Pre-run cost estimate on 44-question golden set (requires live Gemini run)
- [Gated] Execute real eval run with `GEMINI_API_KEY`
- [Gated] Record empirical results in `docs/decisions.md`
- [Gated] Hybrid retrieval decision recorded
- [Gated] Extraction accuracy eval executed against live model
- [Gated] Quality floor ratchets raised

## Item 3 — Deployable Infrastructure & Cloud Run Gating
- [Verified] Move `REDIS_URL` to Secret Manager integration
- [Verified] Add extraction DLQ alert policy
- [Verified] Tighten worker GCS IAM roles in Terraform
- [Verified] Add Memorystore Redis Terraform resource
- [Verified] Verify Pub/Sub push endpoint / OIDC audience match
- [Verified] Unit tests for worker entrypoints (84%-98% branch coverage)
- [Gated] Live cloud apply and smoke test on live GCP project (blocked on external GCP billing & credentials)

## Item 4 — Production Observability & Alerting
- [Verified] Add circuit breaker alert policy
- [Verified] Add platform spend ceiling alert policy
- [Verified] Add worker/extraction error rate alerts
- [Verified] Fix structured logging for uvicorn/worker
- [Verified] Expand operational runbook (`docs/runbook.md`)

## Item 5 — Billing & Tiered Access (Stripe)
- [Verified] Real Stripe SDK integration (`stripe>=11.0,<12.0`)
- [Verified] Emergency fail-closed webhook validation (`stripe_webhook_secret` required)
- [Verified] Startup runtime validation rejects `local_billing=True` outside development
- [Verified] Tier configuration (`subscription_tiers`, migration 021)
- [Verified] Dynamic budget lookup by tier with Redis cache
- [Verified] Webhook idempotency via `stripe_events`
- [Verified] Admin billing visibility

## Item 6 — Email Verification & Account Lifecycle
- [Verified] Email verification token generation and confirmation flow
- [Verified] Rate-limit verification email sends
- [Verified] Atomic verification token consumption
- [Verified] Unverified tenant budget restrictions
- [Verified] Automated purge of unverified accounts past 30 days (`purge_unverified_accounts()`)

## Item 7 — Admin Console Front-End
- [Verified] Tenant list view with usage stats
- [Verified] DLQ inspector view
- [Verified] Failed-ingestion triage view
- [Verified] Extraction review queue view
- [Verified] Strict RBAC gating verification (`owner` / `admin`)

## Item 8 — Human-in-the-Loop Review UI
- [Verified] Tenant-facing vs. admin-only review UI policy
- [Verified] Editable extraction form
- [Verified] Correction audit history (`extraction_corrections`, migration 022)
- [Verified] Field confidence threshold recalibration

## Item 9 — Second Extraction Schema (Commercial Contracts)
- [Verified] Schema choice decision: Commercial Contracts (`ContractExtraction`)
- [Verified] Keyword & Gemini classification routing
- [Verified] Contract extraction Pydantic model with validation
- [Verified] 20-document golden set (`evaluation/contract-golden-set.json`)
- [Verified] Unit test suite (`tests/unit/test_contract_extraction.py`)

## Item 10 — Reranking Decision
- [Retracted] Previous synthetic accuracy figures retracted in Round 5 audit
- [Implemented] Single-stage pgvector retrieval remains production baseline
- [Gated] Two-stage reranking empirical benchmark pending live evaluation run

## Item 11 — CI/CD Hardening
- [Verified] Container image vulnerability scanning (Trivy) in CI
- [Verified] Migration sanity check in CI
- [Verified] Automated staging smoke test gate
- [Verified] Python lockfile committed (`uv.lock`)
- [Verified] Pin external test container digest
- [Verified] Mechanical decisions integrity gate (`scripts/verify_decisions.py`)

## Item 12 — GDPR & Compliance
- [Verified] Privacy Policy and Terms of Service active (`docs/privacy-policy.md`)
- [Verified] Automated data retention policy and GCS lifecycle rules documented
- [Verified] Complete GDPR data export endpoint (`GET /auth/account/export` with chunks, storage URIs, billing events)
- [Verified] Third-party subprocessor catalog (`docs/subprocessors.md`)
- [Verified] 30-day unverified account purge routine (`purge_unverified_accounts()`)

## Item 13 — API Versioning & OpenAPI Contract
- [Verified] Public endpoints mounted under `/v1/` prefix with root aliases
- [Verified] Canonical OpenAPI 3.1 contract committed to `docs/openapi.json`
- [Verified] Mechanical OpenAPI drift check in CI (`python scripts/export_openapi.py --check`)
- [Verified] API deprecation policy documented (`docs/api-policy.md`)

## Item 14 — Disaster Recovery Validation
- [Verified] Point-in-time recovery (PITR) drill documented
- [Verified] RTO/RPO objectives formally documented in `docs/runbook.md`
- [Verified] Redis data-loss impact analysis documented
- [Verified] Automated backup verification script (`scripts/verify_backup.py`)

## Item 15 — End-to-End Integration Tests
- [Verified] Extended deploy smoke test suite (`scripts/deploy_smoke_test.py`)
- [Verified] Cross-tenant isolation probes
- [Verified] Extraction pipeline end-to-end test in local emulator

## Item 16 — Performance Baseline
- [Retracted] Previous synthetic latency & concurrency figures retracted in Round 5 audit
- [Implemented] Locust load test scenario committed (`scripts/locustfile.py`)
- [Verified] Architectural sizing and HNSW threshold policy documented
- [Gated] Live Cloud Run load test against live staging infrastructure
