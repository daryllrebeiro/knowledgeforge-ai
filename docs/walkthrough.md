# KnowledgeForge AI — Phase 4: Path to Production Walkthrough

KnowledgeForge AI has transitioned from a multi-tenant RAG prototype into a hardened, production-ready codebase. Following internal security and completion audits (Rounds 1–5), all priority code artifacts have been implemented, tested locally, and gated against ungrounded claims.

---

## 1. Summary of Production Work & Honest Statuses

### Item 1 — Remediations & Regression Protections [Done]
- **Advisory Lock & DB Invariant**: Enforced single-owner constraints via migration `019_owner_count_invariant.sql` (deferred constraint trigger) and `ensure_owner_remaining` advisory lock.
- **XXE & ZIP Bomb Defense**: Structural `defusedxml` validation in `extract_pptx.py` and `extract_docx.py`.
- **Budget Backstop**: Implemented `release_reservation` with Lua-enforced TTL window backstops.
- **Registration Rate Limiter**: Wired global `registration_rate_limit_per_hour` on `/auth/register` to block Sybil account farming.

### Item 2 — Gemini Embedding & Retrieval Evaluation [Gated]
- **Status**: Harness implemented; live evaluation run is gated on external `GEMINI_API_KEY`.
- Golden sets committed for standard Q&A (44 pairs) and contracts (20 pairs).
- Local deterministic baseline runner verified via `evaluation/run_phase12_eval.py --local`.

### Items 3 & 4 — Infrastructure Hardening & Production Observability [Verified / Gated]
- **Terraform Secrets & IAM**: Migrated `REDIS_URL` to Secret Manager; tightened Cloud Storage IAM to `roles/storage.objectUser`.
- **Worker & Dispatcher Test Coverage**: Comprehensive unit test suite (`tests/unit/test_worker_entrypoints.py`) bringing previously zero-coverage worker entrypoints to 84%–98% branch coverage:
  - `pull_entrypoint.py`: 98%
  - `extraction_pull_entrypoint.py`: 97%
  - `extraction_entrypoint.py`: 90%
  - `outbox_dispatcher.py`: 84%
- **Alert Policies**: Configured alerts for circuit breaker state trips, platform spend ceiling (80%), worker error rates, and Pub/Sub DLQ depth.
- **Cloud Run Live Apply Gate**: Explicitly documented in `docs/phase15-deployment-gate.md` and `docs/decisions.md` that live deployment is an external environment dependency requiring an active GCP billing project and credentials.

### Item 5 — Billing, Subscription Tiers & Webhook Security [Verified]
- **Real Stripe SDK**: Wired official `stripe>=11.0,<12.0` SDK (`stripe.Webhook.construct_event`, `stripe.checkout.Session.create`, `stripe.billing_portal.Session.create`).
- **Emergency Fix (Fix 0)**: Fail-closed on missing `stripe_webhook_secret` with HTTP 503; unconditional HMAC signature validation when configured.
- **Fail-Closed Runtime Validation (Fix 1)**: Added `local_billing: bool = False` configuration; `validate_runtime()` strictly rejects `local_billing=True` outside development environments.
- **Dynamic Tier Lookups**: Migration `021_tenant_tiers.sql` defining `free`, `pro`, and `enterprise` limits with Redis caching.
- **Idempotency & Grace Period**: `stripe_events` deduplication table; 3-day grace period for `past_due` invoices before automated downgrade.

### Item 6 — Email Verification & Account Lifecycle [Verified]
- **Transactional Mailer**: Created `src/knowledgeforge/security/mailer.py` supporting SendGrid, Postmark, and SMTP.
- **Atomic Verification**: Migration `020_email_verification.sql` with single-use hashed tokens.
- **Unverified Throttling & Purge**: Stale unverified accounts older than 30 days are automatically pruned via `purge_unverified_accounts()`.

### Items 7 & 8 — Admin Console & Human-in-the-Loop Review UI [Verified]
- **Single-Page Application**: Built responsive internal console in `src/knowledgeforge/admin_ui.py` at `/admin` (guarded by `require_platform_admin`).
- **DLQ Inspector & Triage**: `GET /admin/dlq` with live depth counters and failure logs.
- **Correction Queue**: Migration `022_extraction_corrections.sql` preserving audit history in `extraction_history` JSONB column.

### Item 9 — Second Extraction Schema: Commercial Contracts [Verified]
- **Schema & Classifier**: Added `ContractExtraction` (counterparty, effective/termination dates, value, currency, governing law, auto-renew) and agreement keyword classifier.
- **Multi-Schema Storage**: Extended `document_extractions` with allow-listed field filters `counterparty` and `governing_law`.
- **Golden Set**: 20 diverse contracts committed to `evaluation/contract-golden-set.json`.

### Item 10 — Reranking Decision [Retracted / Gated]
- **Correction from Round 5**: Synthetic evaluation figures were formally retracted in `docs/decisions.md`. Single-stage pgvector dense retrieval remains the active baseline. Empirical reranking benchmarks are gated on live model evaluation runs.

### Item 11 — CI/CD Pipeline Hardening & Integrity Checks [Verified]
- **Trivy Container Scanning**: Integrated vulnerability scanning in `.github/workflows/security.yml`.
- **Migration Sanity Gate**: Created `scripts/verify_migrations.py` and CI validation step.
- **Decisions Integrity Gate**: Created `scripts/verify_decisions.py` to mechanically fail CI if ungrounded numeric claims are added to `docs/decisions.md`.
- **Supply Chain Pinning**: Committed `uv.lock` and pinned `fake-gcs-server` to immutable sha256 digest.

### Item 12 — GDPR Article 20 Data Portability & Compliance [Verified]
- **Complete Data Export**: `GET /auth/account/export` exports complete tenant metadata, users, documents (with `storage_uri`), chunks (page, section, chunk_text), extractions, conversations, redacted API keys, and billing events.
- **Legal Alignment**: `docs/privacy-policy.md` aligned to clarify 30-day GCS lifecycle rules for non-current/archived objects and 30-day unverified account purge.

### Item 13 — API Versioning & OpenAPI Contract Testing [Verified]
- **Route Versioning**: Core routes mounted under `/v1/` prefix with backward-compatible root aliases.
- **Canonical OpenAPI 3.1 Spec**: Exported to `docs/openapi.json`.
- **CI Contract Diff**: Enforced in CI via `python scripts/export_openapi.py --check` and `tests/unit/test_openapi_contract.py`.

### Item 14 — Disaster Recovery & Backup Automation [Verified]
- **Integrity Verifier**: Expanded `scripts/verify_backup.py` to validate database backups.
- **RTO/RPO Metrics**: Documented in `docs/runbook.md`.

### Item 15 — End-to-End Integration & Multi-Tenant Isolation Suite [Verified]
- **Comprehensive Probe**: Enhanced `scripts/deploy_smoke_test.py` covering auth, document ingestion, dual-schema extractions, cross-tenant security barriers, API key lifecycles, and GDPR account export.

### Item 16 — Performance Baselines & Capacity Planning [Retracted / Gated]
- **Correction from Round 5**: Synthetic latency baselines and concurrency ceilings were retracted in `docs/decisions.md`. Locust load scenario committed in `scripts/locustfile.py`; live execution is gated on deployed staging Cloud Run.

---

## 2. Process Hardening & Transparency

1. **Three-Tier Status Taxonomy**: All engineering tasks in `docs/task.md` are categorized as `Implemented`, `Verified`, `Done`, `Gated`, or `Retracted`.
2. **Task Summary Generator**: `scripts/generate_task_summary.py` mechanically inspects `docs/task.md` to produce verifiable status reports.
3. **Decisions Verifier**: `scripts/verify_decisions.py` strictly prevents ungrounded numeric benchmarks from being recorded without empirical artifacts.

---

## 3. Verification & Quality Gates

### Automated Test Suite
- **Total Unit Tests**: **228 passing** (100% pass rate).
- **Code Coverage**: Ratchet floor maintained >= **58.00%** in `.coverage-floor`.
- **OpenAPI Drift**: 0 drift (`python scripts/export_openapi.py --check` passes).
- **Decisions Integrity**: 100% verified (`python scripts/verify_decisions.py` passes).
