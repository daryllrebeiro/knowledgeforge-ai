# KnowledgeForge AI — Phase 5 Task Tracker: From Claimed to Verified

Status Taxonomy:
- **Implemented**: Code and configuration written and committed; passes unit tests against mocks.
- **Verified**: Exercised against a real external system or real infrastructure, with specific evidence cited.
- **Done**: Verified, plus survived a targeted adversarial audit pass.
- **Gated**: External dependency blocked (credentials, live cloud billing, or external API quota).
- **Retracted**: Unsubstantiated claims removed following audit findings.

---

## Item 1 — Ship the Emergency Fix and the Full Round 5 Remediation Set
- [Done] Fix 0 verified: unsigned webhook POST is rejected with 503 and tenant tier unchanged when `stripe_webhook_secret` unset
- [Done] Round 5 remediation set committed and pushed (Fix 0, Fix 1, Fixes 2-4 retractions & gates, Fix 5 worker tests, Fix 6 GDPR, Fix 7 OpenAPI)
- [Done] Process fixes A–D in place: citation check, task-based summary generator, three-tier vocabulary, human sign-off policy

## Item 2 — Run the Real Gemini Evaluation
- [Implemented] Evaluation harness committed (`evaluation/run_phase12_eval.py`) with local deterministic smoke test passing
- [Gated] Real eval run against all 3 chunking profiles with live `GEMINI_API_KEY` (blocked: Secret Manager key returned 429 RESOURCE_EXHAUSTED)
- [Gated] Real contract and invoice extraction accuracy evaluation via `check_extraction_accuracy.py` (blocked on API quota)
- [Gated] Record empirical Hit@5, correctness, refusal accuracy, and token costs in `docs/decisions.md`
- [Gated] Make hybrid-search and confidence-threshold decisions based on empirical numbers

## Item 3 — Actual Infrastructure Deployment
- [Implemented] Terraform configuration (`terraform/main.tf`), Dockerfiles, and deployment workflow (`.github/workflows/deploy.yml`)
- [Gated] Live `terraform apply` against GCP project (blocked on user approval and live provisioning confirmation per deployment gate)
- [Gated] Full document lifecycle (register -> upload -> ready -> ask -> delete) against live Cloud Run deployment

## Item 4 — Real Stripe Integration, Fail-Closed by Construction
- [Verified] Real `stripe>=11.0,<12.0` SDK integration (`stripe.Webhook.construct_event`, `stripe.checkout.Session.create`)
- [Verified] `LOCAL_BILLING` flag refused outside development by `validate_runtime()`, preventing silent mock fallback
- [Verified] Webhook idempotency and signature rejection verified via unit tests
- [Gated] Real test-mode checkout session, portal session, and webhook round trip (blocked on live Stripe test keys `sk_test_...`)

## Item 5 — Zero-Coverage Worker/Dispatcher Modules Get Real Tests
- [Implemented] `pull_entrypoint.py` claim/lease, already-claimed, and fatal error handling (98% branch coverage)
- [Implemented] `extraction_pull_entrypoint.py` claim/lease, model failure, and error handling (97% branch coverage)
- [Implemented] `extraction_entrypoint.py` duplicate and malformed message handling (90% branch coverage)
- [Implemented] `outbox_dispatcher.py` batch dispatch, lease acquisition, and publish failure handling (84% branch coverage)

## Item 6 — Observability Wired to and Verified Against Real Infrastructure
- [Implemented] Cloud Monitoring alert policies defined in `terraform/main.tf` for circuit breaker, platform spend, DLQ, and error rate
- [Implemented] Structured JSON logging standardized across API, uvicorn, and background workers
- [Gated] Alert policy firing and notification channel delivery verified against live deployed system (blocked on Item 3)

## Item 7 — GDPR Export Completeness and Retention Enforcement
- [Verified] `export_tenant_data` covers all 9 tenant-scoped entities (`tenant`, `users`, `documents`, `chunks`, `extractions`, `conversations`, `api_keys`, `billing_events`, `invitations`)
- [Verified] Retention claims in `docs/privacy-policy.md` verified against enforcing code: 30-day GCS lifecycle rule and `purge_unverified_accounts()`
- [Verified] Comprehensive unit test suite in `tests/unit/test_gdpr.py` passing (6/6 tests)

## Item 8 — Disaster Recovery and End-to-End Tests Against Real Staging
- [Implemented] Point-in-time recovery documentation and automated backup integrity checker (`scripts/verify_backup.py`)
- [Implemented] Deployed-target smoke test suite (`scripts/deploy_smoke_test.py`)
- [Gated] Live PITR restore drill against real staging Cloud SQL instance (blocked on Item 3)
- [Gated] Deployed smoke test suite passing against live staging URL (blocked on Item 3)

## Item 9 — Build the Verified-Status Gate as Tooling, Not Policy
- [Done] Mechanical decisions citation verifier (`scripts/verify_decisions.py`) with negative and positive tests (`tests/unit/test_decisions_integrity.py`)
- [Done] Task-driven summary generator (`scripts/generate_task_summary.py`) with automated parser tests (`tests/unit/test_task_summary.py`)
- [Done] Explicit human sign-off policy recorded in `docs/decisions.md` with tooling enforcement citation

## Item 10 — Close the Second Extraction Schema and Reranking Decision Using Real Data
- [Implemented] Commercial contract extraction schema (`ContractExtraction`) and 20-document golden set committed
- [Implemented] Single-stage pgvector dense retrieval baseline active
- [Gated] Empirical contract extraction accuracy evaluation using live Gemini calls (blocked on Item 2 API quota)
- [Gated] Empirical reranking adoption evaluation using live retrieval numbers (blocked on Item 2 API quota)
