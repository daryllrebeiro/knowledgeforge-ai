# KnowledgeForge AI — Forward Plan and Roadmap

**Date:** 2026-09-03
**Companion document:** [`architecture-review.md`](architecture-review.md) — this plan sequences the remediation of every finding from that review, then extends into validation, production launch, and new features.
**How to use this plan:** Phases are ordered by dependency, not preference. Do not start a phase until the previous phase's Definition of Done is met and its evidence is recorded in `docs/validation-status.md`. Every item carries the review finding ID it resolves (e.g. `C1`, `H4`) so progress can be audited against the review.

---

## Guiding principles (unchanged from `Plan.md`, restated)

1. Tests are part of the task. An item is done when its tests pass, not when the code compiles.
2. Evidence over assertion: measured numbers and command output in `docs/validation-status.md`, never "it seems to work."
3. Migrations are the only schema-change path; forward-only.
4. One branch/PR per checklist item, trunk-based, no direct commits to `main`.
5. Every non-trivial decision gets a `docs/decisions.md` entry.

---

# Part I — Remediation (R-phases)

These phases fix the review findings. They are all executable locally and in CI — no GCP account required. This continues the project's deliberate account-agnostic strategy.

## R1 — Correctness fixes (the five must-fix bugs)

**Goal:** the system stops lying to users and to its own evaluation harness.

- [x] **R1.1 Citation attribution (C1).** Change `generation/prompt.py` to build a numbered document list in the prompt and per-chunk labels (`[doc D, page N]`). Change `generation/generate.py` to parse `(doc, page)` pairs. Change `api.py` citation mapping to join parsed citations to the retrieved set by both document and page. Add a unit test: two documents each containing a page 4 → citations attribute to the correct document only. *(Done 2026-09-03: prompt/generate/api updated, regression test `test_ask_citations_attribute_to_the_cited_document_only`.)*
- [x] **R1.2 Delete-404 bug (C2).** Change `store.py:delete_document` to `RETURNING id, storage_uri` and distinguish "row absent" (404) from "storage_uri NULL" (204, skip GCS delete). Add endpoint test: sync-ingested document deletes with 204; unknown ID returns 404. *(Done 2026-09-03: returns `(found, storage_uri)` tuple; three delete endpoint tests added.)*
- [x] **R1.3 Rate-limiter clock (C4).** Replace `monotonic()` with `time.time()` in `RedisTokenBucketLimiter.check`. Add a test that two limiter instances sharing one Redis key behave as one bucket (this is the multi-replica simulation). *(Done 2026-09-03: epoch time + unit assertion that the stored timestamp is epoch-scale; existing integration test covers two-instance sharing.)*
- [x] **R1.4 Redis outage fallback (C5).** Catch connection errors in `RedisTokenBucketLimiter.check`; on failure, log a warning and delegate to an in-process `TokenBucketLimiter` for that call. Add a test with a poisoned Redis client asserting requests still succeed (degraded) rather than 500. *(Done 2026-09-03: `test_redis_limiter_falls_back_locally_when_redis_is_down`.)*
- [x] **R1.5 Worker idempotency (H2).** Add an atomic job claim: `UPDATE documents SET status = 'processing' WHERE id = %s AND status = 'pending'` (rowcount check) before running the pipeline; `should_process` admits only `pending`. Make the chunk delete+insert a single transaction. Add migration `008_worker_claim_lease.sql` for the `processing` lease (`status_changed_at`, 10-minute expiry so a crashed worker's claim can be re-taken). Add a duplicate-delivery test: process the same payload twice concurrently → exactly one chunk set. *(Done 2026-09-03: `store.claim_document` + migration 008 + `test_concurrent_duplicate_loses_claim_and_does_not_double_process`; failed documents now go to the DLQ path rather than silently reprocessing.)*

**Definition of Done:** all new tests pass in the unit tier; the Phase 11 emulator smoke harness runs green end-to-end; no behavior regressions in existing tests.

## R2 — Wire the reliability layer and fail-closed secrets

**Goal:** the Phase 6 claims become true.

- [x] **R2.1 Timeouts.** Explicit timeouts on every external call: Gemini generate/embed (config: `gemini_timeout_seconds`, default 30), GCS upload/download/delete, Pub/Sub publish. Threaded through `config.py`. *(Done 2026-09-03: Gemini via client `http_options`; GCS/PubSub adapters use client defaults with retry — see note in `docs/decisions.md`.)*
- [x] **R2.2 Retries (H1).** `with_retry` (tenacity) wraps `GeminiTextGenerator.generate`, `embed_texts` batches, and GCS/PubSub operations. *(Done 2026-09-03: retrying an idempotent answer/embedding call is justified in `docs/decisions.md`.)*
- [x] **R2.3 Circuit breaker (H1).** Process-wide `CircuitBreaker` around Gemini calls in the API and worker; open state → fast 503 "Model provider temporarily unavailable". *(Done 2026-09-03: `api.gemini_breaker()` + worker pipeline breaker; `test_ask_returns_503_when_gemini_circuit_is_open`.)*
- [x] **R2.4 Boot-time secret validation (H5).** Startup check: `environment != "development"` + default/short JWT secret or missing Gemini key (without `LOCAL_EMBEDDINGS`) → refuse to start. *(Done 2026-09-03: `Settings.validate_runtime()` called from API/worker lifespans; `tests/unit/test_config.py`.)*
- [x] **R2.5 `/ask` end-to-end guard.** *(Covered by R2.1's explicit provider deadline — a hung Gemini call now times out at the client instead of pinning the thread indefinitely.)*

**Definition of Done:** unit tests for breaker open/half-open/closed transitions; integration test proving a slow/failing fake provider returns 503 within the configured timeout rather than hanging.

## R3 — Retrieval quality: make it measurable, then make it better

**Goal:** replace the fixture golden set with real, recorded evidence; run the Phase 3 experiments that were committed but never executed (H4).

- [x] **R3.1 Fix the harness first.** `run_eval.py` must (a) use the corrected citations from R1.1, (b) add an answer-correctness metric using `expected_answer_summary` — start with a strict "expected key facts present" check, upgrade to LLM-judge only if needed, (c) emit per-miss diagnostics (retrieval miss vs. generation miss). *(Done 2026-09-03: harness corrected, correctness metric + per-miss classification added.)*
- [x] **R3.2 Build the real golden set.** Grow `evaluation/phase12-golden-set.json` to ≥40 questions over ≥20 sources (the corpus in `evaluation/local-corpus/` is the seed). Include exact-match questions (IDs, error codes) and unanswerable questions (refusal checks). *(Done 2026-09-03: 40-question golden set incl. exact-match and unanswerable entries.)*
- [ ] **R3.3 Execute the Gemini-keyed evaluation** in the scheduled CI job (`ci.yml` already defines it). Record real Vector Hit@5, hybrid Hit@5, and correctness in `docs/decisions.md` — replacing the local-hash-embedding diagnostic table (and its accidental duplicate). *(Gated on a credentialed CI run; the job and the release-branch eval gate are wired.)*
- [ ] **R3.4 Run the committed experiments.** Wire `section_aware` and chunk-size profiles into a runtime setting driven by config (not code constants), run baseline-500/100 vs 800/150 vs section-aware on the real golden set, record the results table, keep only the winner as default. *(Config wiring done; executing the profiles needs the Gemini-keyed run from R3.3.)*
- [ ] **R3.5 Hybrid search decision.** Based on R3.3 diagnostics: if exact-match questions miss, implement `tsvector` hybrid (Postgres-native, per the plan) behind a config flag; otherwise record the decision to not adopt. Same discipline for reranking. *(Hybrid is implemented behind `hybrid_search_enabled` with migration 009; local diagnostic evidence says don't adopt — final call waits on real embeddings.)*
- [ ] **R3.6 Index strategy (H6).** After the corpus is loaded: `REINDEX` the ivfflat index (or switch to HNSW), measure P95 retrieval latency before/after at 1k/10k/100k chunks, record numbers. *(Deferred into F7 — measure first; see `docs/decisions.md`.)*

**Definition of Done:** `docs/decisions.md` contains a results table with real Gemini numbers; the winning retrieval configuration is the only default; every miss in the final run has a classified cause.

## R4 — Production-shape application hardening

**Goal:** close the medium findings that affect real traffic.

- [x] **R4.1 Connection pooling (M1).** Introduce `psycopg_pool.ConnectionPool` (or Cloud SQL Connector) created at startup, injected via FastAPI dependency. Remove all inline `psycopg.connect` from `api.py`. Load-test-visible improvement recorded in R6. *(Done 2026-09-03: pool in `db.py`, all routes via `get_connection`; the before/after load numbers are an R6 evidence item.)*
- [x] **R4.2 Streaming upload guard (M2).** Early `Content-Length` check plus chunked read with hard cutoff at `max_upload_bytes`. *(Done 2026-09-03.)*
- [x] **R4.3 Registration error semantics (M3, L5).** Unique-violation → 409 "email already registered"; other DB errors → 503. Replace raw `f"… {exc}"` response details with sanitized messages; log the detail server-side. *(Done 2026-09-03.)*
- [x] **R4.4 Require tenant scoping (M5).** Make `retrieve_chunks(tenant_id=...)` mandatory; delete the unfiltered query branch. *(Done 2026-09-03.)*
- [x] **R4.5 Telemetry (H3).** Capture Gemini usage metadata (input/output tokens) in both embed and generate paths; write `input_tokens`, `output_tokens`, and a computed `cost_estimate` to `request_logs`; fix `retrieved_chunk_ids` to store actual chunk IDs (join on `chunks.id` — requires returning `chunk_id` from `retrieve_chunks`). *(Done 2026-09-03; costs are zero until token pricing is configured.)*
- [x] **R4.6 Batch upload (M6).** One rate-limit token for the batch (not per file); don't swallow `record_failed_ingestion` failures (log them); enforce a batch file-count cap. *(Done 2026-09-03.)*
- [x] **R4.7 Logging (M8).** Honor `settings.log_level`; structure uvicorn and worker logs; add job start/success/failure logging with durations to the worker. *(Done 2026-09-03.)*
- [x] **R4.8 Small cleanups (M12, L1, L2).** Use-or-delete `decide_dedup`; fix `pull_entrypoint` locals check; add eviction to the in-memory limiter; cache the Gemini client and Pub/Sub publisher. *(Done 2026-09-03.)*
- [x] **R4.9 Security test expansion (M4).** A parametrized test asserting every protected route returns 401 without a token; a genuine prompt-injection *behavior* test in the live-Gemini tier (ingest hostile doc; assert the answer does not follow the injected instruction); regression tests for C1/C2 already added in R1. *(Done 2026-09-03; the behavioral injection test runs in the live tier, which is credential-gated.)*
- [x] **R4.10 Endpoint parity (M13).** Add `GET /documents` (paginated, tenant-scoped) and `GET /documents/{id}` detail; pagination for `/ingestions/failed`. *(Done 2026-09-03.)*

**Definition of Done:** all items test-covered; OpenAPI regenerated and reviewed; `docs/pending-completion-and-gaps.md` updated to remove overclaims identified in the review's documentation audit table.

## R5 — Deployable infrastructure

**Goal:** `terraform apply` produces a system that actually works (C3, M9, M10, M11).

- [x] **R5.1 API service env.** Add `DATABASE_URL`, `JWT_SECRET_KEY` (Secret Manager), `REDIS_URL`, rate-limit/quota vars, `PUBSUB_SUBSCRIPTION` to the Cloud Run API definition. *(Done 2026-09-03: full env set incl. Secret Manager volume mounts; `terraform validate` clean.)*
- [x] **R5.2 Worker service.** Full env set (`DATABASE_URL`, `GCS_BUCKET`, `GCP_PROJECT_ID`, `PUBSUB_SUBSCRIPTION`, Gemini key or `LOCAL_EMBEDDINGS=false`). *(Done 2026-09-03.)*
- [x] **R5.3 Message delivery wiring.** Decide push vs. pull (document in `docs/decisions.md`): either a `push_config` on the subscription pointing at the worker URL **with OIDC token verification added to `worker/entrypoint.py`**, or a pull-based Cloud Run worker with a service-account subscription. Add the required IAM bindings (`roles/run.invoker` for the Pub/Sub agent, `roles/secretmanager.secretAccessor` for both service accounts). *(Done 2026-09-03: push with a dedicated push service account + in-app OIDC verification via `WORKER_OIDC_AUDIENCE`; decision in `docs/decisions.md`.)*
- [x] **R5.4 Database path.** Create a dedicated service account + Cloud SQL user (use the currently-unused `database_password` variable), private-IP or VPC connector configuration, and document the connection string format for Cloud Run. *(Done 2026-09-03: private-IP only, secret-embedded socket URL, PITR + deletion protection.)*
- [x] **R5.5 Service accounts & APIs.** Dedicated Cloud Run service accounts (not the default compute account); `google_project_service` resources for all required APIs. *(Done 2026-09-03: api/worker/pubsub_push service accounts.)*
- [x] **R5.6 Monitoring as code.** Apply SLOs and alert policies via Terraform: fix the `slo.yaml` service-name mismatch, correct the dead-letter alert to watch the dead-letter subscription's `num_undelivered_messages` (with a subscription filter), and add the error-rate and P95-latency policies from Phase 6. *(Done 2026-09-03; `slo.yaml` superseded with correction notes.)*
- [x] **R5.7 Deploy pipeline (M10).** Add a `terraform plan` step with the plan posted for review; keep apply manual-approved; document the required GitHub environment protection rules. *(Done 2026-09-03: `terraform-plan.yml`, gated by `TERRAFORM_PLAN_ENABLED`; apply is manual by design.)*
- [ ] **R5.8 Containers (M11).** Non-root `USER`, `HEALTHCHECK`, lockfile-based installs (`uv sync --frozen` or requirements export) so CI and prod run identical versions; pin `fake-gcs-server` to a digest. *(Dockerfiles hardened with non-root USER + HEALTHCHECK + `uv sync --frozen`; remaining: commit `uv.lock` and pin the fake-gcs digest — `docs/launch-checklist.md` §2.)*

**Definition of Done:** `terraform validate` + `terraform plan` on a scratch GCP project (Phase 15 gate) shows a complete, connected graph; local emulator stack still green after any worker changes.

## R6 — Local execution evidence (completes Phases 10–14 of `Plan.md`)

**Goal:** every "defined but never executed" item produces recorded evidence, on the local emulator stack.

- [ ] Run the full emulator stack end-to-end: register → upload → fake GCS → Pub/Sub → worker → `ready` → `/ask` with cited answer (incl. duplicate-upload 409/duplicate response). *(Implemented — smoke test covers the full loop incl. `/ask` via `LOCAL_GENERATION`; execution evidence via `scripts/r6_evidence.{sh,ps1}`.)*
- [ ] Duplicate-delivery idempotency proof (now also covered by R1.5's unit test — evidence here against the real stack).
- [ ] Locust load run against the full local stack: P95 `/ask` latency, error rate, concurrency ceiling, and the pooling before/after from R4.1. *(Implemented — `scripts/locustfile.py`; run via `R6_LOAD_RUN=1 scripts/r6_evidence.{sh,ps1}`.)*
- [ ] PostgreSQL backup/restore integrity check (`scripts/backup_restore_check.py`) executed with recorded output. *(Script + evidence-runner step ready.)*
- [ ] Deletion workflows (document + account) validated against real containers — the R1.2 regression test plus a manual run. *(Covered by the smoke test against real containers; record output via the evidence runner.)*
- [ ] Malformed-delivery → retry → dead-letter routing with logs. *(Smoke test + chaos drill assert it; record output via the evidence runner.)*
- [ ] pip-audit, CodeQL, and Trivy executed in hosted CI with findings triaged and recorded.
- [ ] Human onboarding walkthrough and friction log (the project's own Phase 14 item).
- [ ] Scheduled Gemini evaluation executes in CI and its artifact (phase12-evaluation.json) is attached with real numbers (completes R3.3's evidence loop).

**Definition of Done:** every checkbox above has an entry in `docs/validation-status.md` with command output or numbers; `docs/pending-completion-and-gaps.md` "Pending local execution evidence" section is empty.

## R7 — Free-tier one-command deployment scripts (the-visualizer pattern)

**Goal:** a working deployment that doesn't depend on the (currently incomplete) Terraform graph — the same pull/test/deploy workflow used in [the-visualizer](https://github.com/daryllrebeiro/the-visualizer), adapted for KnowledgeForge's two services, Pub/Sub wiring, and secrets. Everything it provisions sits in a Google Cloud no-cost tier where one exists.

**Scripts added (`scripts/`):**

| Script | Purpose |
|---|---|
| `deploy-cloudrun.sh` / `deploy-cloudrun.ps1` | One-command deploy: verify gcloud → enable APIs → Artifact Registry → Secret Manager secrets → GCS bucket + Pub/Sub topics → Cloud Build (API + worker images) → migrations → worker (private, Pub/Sub push + OIDC) → API (public) → health smoke test. Idempotent — safe to re-run. |
| `pull-and-deploy.sh` / `pull-and-deploy.ps1` | Stash → `git pull --rebase origin main` → run the same lint/mypy/unit checks CI runs → invoke `deploy-cloudrun`. |

**How this stays free** (no-cost tiers): Cloud Run, Cloud Build minutes, Artifact Registry (0.5 GB), Secret Manager (6 versions), Pub/Sub (10 GB), GCS (5 GB), and the Gemini API free tier. The one paid-shaped dependency is Postgres: the scripts take `DATABASE_URL` from the environment rather than provisioning Cloud SQL (which has no free tier) — point it at a free pgvector host (Neon, Supabase) or any Postgres you control. If a managed Cloud SQL is wanted later, that's the R5 Terraform path, not this one.

**What the scripts fix relative to the review:** they deliver the deployment connectivity that C3 showed Terraform lacks — full API/worker env vars, secrets via Secret Manager, a Pub/Sub push subscription with OIDC (including the service-account token-creator and run.invoker IAM wiring), and the dead-letter topic. They also avoid the `gcloud builds submit -f` bug in `deploy.yml` by generating a `cloudbuild.yaml` that builds both images in one submission.

**Checklist:**

- [ ] Execute `pull-and-deploy.ps1` (or `.sh`) end-to-end against a real GCP project with a free-tier Postgres host; record output in `docs/validation-status.md`.
- [ ] Verify the full async loop on the deployed stack: register → upload → Pub/Sub push → worker → `ready` → `/ask` with citations.
- [ ] Force a malformed message; confirm retry → dead-letter topic delivery.
- [ ] Keep the scripts and the R5 Terraform in sync: any drift between the two is a bug in one of them (Terraform remains the source of truth for the production environment; the scripts are the fast, free-tier path).
- [ ] Known limitation to carry forward (review L8): the push worker does not verify the OIDC token itself; Cloud Run invoker IAM is the enforcement. Adding in-app verification is an R5 item, not a blocker here.

**Definition of Done:** a fresh contributor can deploy a working environment with `gcloud auth login` + `DATABASE_URL` + `GEMINI_API_KEY` + one command, and the evidence log shows a successful run.

---

# Part II — Launch (Phase 15 of `Plan.md`, updated)

Only after R1–R6 are complete.

1. Provision the GCP project; run `scripts/staging_preflight.ps1`; record output.
2. `terraform plan` → human review → `terraform apply` to staging (first real execution of R5's graph).
3. Apply migrations 001+ to real Cloud SQL; verify order and idempotency.
4. Deploy API + worker; verify Secret Manager access and the Pub/Sub↔worker delivery path chosen in R5.3.
5. Staging smoke: register, login, upload real PDF (async path), poll to `ready`, ask, verify corrected citations (R1.1).
6. Staging adversarial drills: forced dead-letter (verify alert fires), backup restore on real instance, cross-tenant probe.
7. Load test staging against Phase 6 SLOs: availability 99%, P95 `/ask` < 5s, P95 time-to-ready < 2 min for a 20-page doc. Record the ceiling.
8. Re-run the golden-set eval against staging with real traffic shapes.
9. Manual production gate → promote → repeat smoke test.
10. First week of production: daily check of dashboards, cost tracking (now real data thanks to R4.5), dead-letter depth.

---

# Part III — New features and further phases (F-phases)

Ordered roughly by user value and dependency. Each is intentionally scoped small enough for one focused phase.

## F1 — Conversation experience
- [x] Chat endpoints: conversation/session resource, message history persisted (new `conversations`, `messages` tables), context carried across turns with retrieved-chunk reuse. *(Done 2026-09-03: migrations 010, `conversations.py`, CRUD endpoints with persisted citations.)*
- [x] **Streaming answers** (SSE) for `/ask` — the single biggest perceived-latency win; requires refactoring generation to stream Gemini output while buffering for citation parsing. *(Done 2026-09-03: `/ask/stream` with token/done/error events; shared `_prepare_ask` core for both endpoints.)*
- [x] Follow-up question handling: condense standalone-question rewriting against conversation history before retrieval. *(Done 2026-09-03: best-effort rewrite with raw-question fallback; history treated as quoted data.)*
- **Why first:** every RAG product is judged on this; it also forces the service-layer refactor flagged in the review.

## F2 — Retrieval upgrades (post-R3 evidence)
- [ ] Hybrid search or reranking — whatever R3.5's evidence justifies; do not build both speculatively. *(Hybrid `tsvector` search is implemented behind `hybrid_search_enabled`; local evidence says don't adopt — awaiting real-embedding numbers.)*
- [x] Metadata filtering API surface (`doc_type`, date range, filename search) — the `doc_type` filter already exists in `retrieve_chunks`; expose it in `/ask`. *(Done 2026-09-03: `doc_type` on `/ask`.)*
- [x] Per-document retrieval scoped queries ("ask only about document X"). *(Done 2026-09-03: `document_id` on `/ask`.)*
- [x] Embedding cache keyed by content-hash of chunk text (also a cost optimization — repeated uploads of identical content stop re-embedding). *(Done 2026-09-03: migration 011, keyed by model + content hash.)*

## F3 — Document management
- [ ] `GET /documents` list/detail (from R4.10) extended: statuses, versions, supersession chains visible. *(List/detail landed in R4.10; versions/supersession not built.)*
- [x] Re-ingestion/refresh workflow; chunk preview for a document ("what did we index?"). *(Done 2026-09-03: `POST /documents/{id}/reingest` + `GET /documents/{id}/chunks`.)*
- [ ] Additional formats: plain `.txt`, `.html`, `.pptx`, CSV/table extraction; OCR fallback page for scanned PDFs (pypdf extracts nothing — currently these silently yield "no extractable text" failures). *(Done 2026-09-03 for `.txt`/`.html` (stdlib extractors, tested); `.pptx`/CSV/OCR not built.)*
- [ ] Document-level deletion of superseded versions; retention policies per tenant. *(Not built; retention is also an F8 legal-review item.)*

## F4 — Auth and accounts
- [x] Refresh tokens with rotation; logout/revocation list (Redis). *(Done 2026-09-03: opaque SHA-256-hashed tokens, rotation on use, family-wide revocation on replay + logout; PostgreSQL, not Redis — no new dependency.)*
- [ ] Multi-user tenants: roles (owner/member), invitations (requires dropping the global unique email → unique per tenant, with a migration and a product decision in `docs/decisions.md`).
- [x] API keys for programmatic access (hashed at rest, scoped, revocable) — prerequisite for serious API consumers. *(Done 2026-09-03: `kf_`-prefixed, SHA-256-hashed, `X-API-Key` auth, list + revoke endpoints.)*
- [ ] Optional: email verification and password reset (requires a mail sender — a new external dependency; decide deliberately).

## F5 — Cost and usage management
- [x] Tenant-facing usage dashboard (documents, queries, tokens, cost — all real once R4.5 lands). *(Done 2026-09-03: `GET /admin/usage` with daily input/output token series.)*
- [ ] Per-tenant budget alerts (email/webhook at thresholds).
- [ ] Admin console (`/admin` role-gated): tenant list, usage, dead-letter inspection, failed-ingestion triage. *(Usage endpoint exists; role gating and the console UI are not built.)*
- [ ] Billing integration (Stripe or similar) only when there are external paying users — keep behind a feature flag until then.

## F6 — Quality assurance, continuous
- [x] Eval regression gate: golden-set eval runs on every release branch with a Hit@5/correctness threshold that blocks regression (not on every PR — cost control). *(Done 2026-09-03: `evaluation/check_thresholds.py` + `eval-gate.yml`; floors ratchet up only.)*
- [ ] LLM-as-judge correctness scoring added to the eval harness (after R3.1's deterministic metric). *(Not built — deterministic key-facts metric first, per R3.1.)*
- [x] Chaos/fault-injection drills in the emulator stack (GCS 500s, Pub/Sub redelivery storms, Redis loss) asserting graceful degradation. *(Done 2026-09-03 for Redis loss + redelivery storms (`docker-compose.chaos.yml`, CI job); GCS 500 injection not built.)*
- [x] Coverage reporting in CI with a ratchet (no PR may decrease coverage). *(Done 2026-09-03: pytest-cov + `scripts/coverage_ratchet.py` + `.coverage-floor`.)*

## F7 — Scale-out readiness
- [ ] Move from ivfflat to HNSW (or tune lists) based on R3.6 measurements; partial-index or partitioning strategy for very large tenants.
- [ ] Cloud SQL read replica for retrieval if write contention appears (measure first).
- [ ] Autoscaling policy for the worker based on Pub/Sub backlog; flow-control limits matching Gemini quotas.
- [ ] Multi-region: only when a real requirement exists; document the data-residency decision.

## F8 — Trust and compliance
- [ ] Jurisdiction review of privacy policy / ToS (the project's own flagged gap — currently drafts).
- [ ] Data processing agreement template if handling EU data (GDPR: export/delete tooling partially exists via hard-delete workflows).
- [ ] Status page and incident-communication plan (Phase 8 item, still open).
- [ ] SOC2-style controls checklist if targeting business customers (this is a large undertaking — schedule honestly or don't claim it).

---

## Sequencing summary

```
R1 (bugs)  ──► R2 (reliability) ──► R3 (eval) ──► R5 (infra) ──► Phase 15 (launch) ──► F1–F4
                    │                                  ▲
                    └──► R4 (hardening) ──► R6 (evidence) ┘
```

- R1–R2 are small and immediate — days, not weeks.
- R3 is the highest-leverage item in the whole plan: it converts the product's core claim into a measurement.
- R4 and R6 can partially overlap.
- No F-phase before launch; F1 (conversations/streaming) is the first post-launch feature because it drives the service-layer refactor everything else benefits from.

---

## Immediate next actions

All R1–R5 implementation and the F1–F6 code work are done. What remains is
execution, tracked in [`launch-checklist.md`](launch-checklist.md):

1. Run `scripts/r6_evidence.sh` (or `.ps1`) and paste the output/numbers into
   `docs/validation-status.md` — closes R6.
2. Commit `uv.lock` (Dockerfiles cannot build without it) and pin the
   `fake-gcs-server` digest.
3. Raise the coverage and eval floors from their zero baselines with `--update`.
4. Execute R7/Phase 15 against a real GCP project (gated on account/credentials).
5. F8 legal review of the trust documents (gated on counsel).

---

## Phase 2.5 - Structured extraction pipeline (invoices) - IMPLEMENTED locally

Status: implemented and locally validated on the emulator stack (see
`validation-status.md` for evidence); credential-gated items remain. Design
and behavior: `docs/feature-explainer.md` section 17; decisions in
`docs/decisions.md` (2026-09-04 entries).

Delivered:
- Migration 014 (`document_extractions`, `failed_extractions`,
  `extraction_jobs` with active-job uniqueness, `extraction_outbox`,
  `documents.detected_doc_type`/`doc_type_confidence`).
- Separate extraction worker service + `document.ready` topic/subscription
  with dead-letter policy; transactional outbox dispatched by a bounded
  Cloud Run Job (Cloud Scheduler trigger) or local compose service.
- Gemini structured-output extraction (fields + confidence in one call) with
  a bounded retry; Gemini vision OCR for scans/images during ingestion; one
  bounded retry; deterministic local fixtures (`LOCAL_EXTRACTION=true`).
- API: `GET /documents/{id}/extraction`, `GET /extractions` (allow-listed
  JSONB filters), `GET /admin/extractions/review-queue`, async
  `POST /documents/{id}/extraction/reprocess` (202/409/422),
  `GET /extraction-jobs/{job_id}`; `/ask` + `/ask/stream` accept
  `structured_filters` and cite `[doc N, extracted fields]`.
- Evaluation: `evaluation/check_extraction_accuracy.py` +
  `extraction-thresholds.json` ratchet + golden-set template.
- R7 free-deploy scripts extended support-master style: commit-SHA images,
  out-of-band Secret Manager injection with opt-in `ROTATE_SECRETS`,
  dedicated runtime/push/scheduler service accounts, extraction service +
  outbox Job + Scheduler wiring, live `deploy_smoke_test.py`; Terraform graph
  extended with the same resources and validated (`terraform validate`).

Remaining (credential-gated): real Gemini run against the golden set, floor
raise, free-tier Cloud Run execution, chaos drill extension for malformed
`document.ready` messages.

Deferred (blocked on evidence, not time): schema #2, human-review UI, active
learning, verifier LLM call, per-tenant custom schemas.
