# Validation status

## Local/CI-complete checks

- Phases 1â€“3: extraction, chunking, prompting, retrieval, evaluation harness, and API contract tests.
- Phase 4: JWT/password tests, protected-route tests, tenant propagation, and prompt-injection coverage.
- Phase 5: job idempotency, async worker pipeline code, status transitions, and cloud adapter configuration.
- Phase 6: structured request logging, request-log persistence, SLO/runbook artifacts,
  and retries/timeouts/circuit breaker wired into the Gemini, storage, and publish
  paths (previously the reliability module existed with tests but was never called â€”
  corrected 2026-09-03; see `docs/decisions.md`). Token/cost telemetry is written by
  the ask and worker paths as of 2026-09-03 (R4.5); costs are zero until
  `GEMINI_INPUT/OUTPUT_TOKEN_COST` reflect real pricing.
- Phase 7: rate-limit, quota, and usage aggregation code/tests.
- Phase 8: Terraform formatting and `terraform validate`.

The GitHub Actions CI workflow runs the full Python suite plus Terraform formatting,
initialization, and validation on every pull request.

## External acceptance still required

These checks cannot be truthfully completed without the target services:

- Postgres/pgvector integration tests against a running database.
- Real Gemini ingestion and golden-set Hit@5 measurements.
- Cloud Storage/Pub/Sub delivery, dead-letter, and retry verification.
- Cloud Monitoring alert trigger verification.
- Terraform apply, Cloud SQL restore, and load-test ceiling measurement.

## Phase 8.5 evidence log

### Task 1 â€” GCP project and API provisioning

Status: blocked at preflight.

Attempted evidence on 2026-08-23:

- No `gcloud` executable was initially available on `PATH`.
- The official Windows x86_64 portable archive was downloaded into the ignored
  project-local `.tools/gcloud/` directory.
- The portable archive could not complete its bootstrap with the bundled workspace
  Python: after adding the missing `PySocks` dependency locally, it still failed while
  importing another SDK dependency (`apitools`).
- No authenticated Cloud SDK context is available.
- No configured GCP project is available.

This task remains blocked. Required next input is a GCP project ID with billing enabled
and credentials that can authenticate Cloud SDK, plus a supported Google Cloud CLI
installation (the official installer is preferred over the incomplete portable runtime).

## Deferred validation policy

GCP provisioning, account registration, authenticated smoke tests, Terraform apply,
Cloud SQL restore, Pub/Sub dead-letter verification, and production promotion are
explicitly deferred. Local implementation, static validation, unit tests, CI checks,
and account-agnostic security hardening may continue without creating or attaching any
user, cloud account, project, or credential.

## Phase 9 account-agnostic evidence

Completed locally on 2026-08-23:

- Redis token-bucket implementation added with an atomic Lua script and an empty-config
  in-memory fallback.
- Authentication rate limiting, deny-by-default CORS, security headers, and `/v1` API
  compatibility are covered by code and tests.
- Dependency audit, CodeQL, and filesystem vulnerability/secret/misconfiguration scan
  workflows are defined without cloud credentials.
- Ruff, formatting, mypy, and 31 pytest tests pass.

Still deferred: a live Redis instance, cloud IAM review, Cloud Monitoring verification,
container registry scanning, backup drills, and real database isolation tests.

## Phase 10 Task 1 evidence

Implemented on 2026-08-23:

- Added `unit`, `integration`, and infrastructure jobs to `.github/workflows/ci.yml`.
- Added a fresh PostgreSQL/pgvector CI service using `pgvector/pgvector:pg17`.
- Added `scripts/apply_migrations.py` to apply migrations 001 through 006 in order.
- Added `tests/integration/test_postgres_tenant_isolation.py`, which inserts two tenants
  and verifies real pgvector retrieval cannot return the other tenant's document.
- Added pytest markers for `integration` and `live` tiers.
- Local static checks and the default unit suite pass.

Local execution note: Docker is not installed in the current workspace, so the real
PostgreSQL test was skipped locally. The CI service definition is present but still
requires one hosted CI run to establish the integration evidence and close Task 1.

## Phase 10 remaining evidence

- Redis was added to `docker-compose.yml` and the CI integration service. Two separate
  Redis limiter clients now share an atomic token bucket in the integration test.
- The live-external CI job is defined for scheduled or main-branch execution and reads
  `GEMINI_API_KEY` only from a CI secret; no local credential is configured.
- Migration policy is documented in `docs/migrations.md` as forward-only with backup
  restore/forward-repair recovery, pending the Phase 13 drill.
- Phase 10 implementation is complete, but hosted CI execution is still needed to
  record real Postgres/Redis and Gemini evidence.
- Added a repository-owned three-document live corpus and scheduled Gemini embedding
  evaluation; it remains credential-gated and has not run locally.

## Phase 11 implementation evidence

Implemented on 2026-08-23:

- Added `docker-compose.full.yml` with PostgreSQL/pgvector, Redis, fake-gcs-server,
  the official Pub/Sub emulator, API, worker, migration, and emulator-init services.
- Added emulator-aware anonymous cloud clients and a pull-based Pub/Sub worker.
- Added deterministic local embeddings so the async worker can run without Gemini or a
  GCP account.
- Added a local cloud initialization script for the storage bucket, Pub/Sub topic,
  subscription, and dead-letter topic.
- Added an executable full-stack smoke harness and explicit malformed-delivery retry
  handling in the pull worker.

Execution remains pending because Docker is not installed in the current workspace.

## Phase 12 implementation evidence

- Added a 20-source repository corpus and 20-question golden set.
- Added baseline and large-chunk retrieval evaluation with Hit@5 measurement.
- Added per-miss classification to distinguish lexical/hybrid-search candidates from
  ranking/reranking candidates.
- Added scheduled Gemini evaluation and artifact upload to CI.
- Added a vector-versus-lexical-hybrid comparison mode; local results are diagnostic and
  are not treated as production-quality evidence.
- Real Gemini numbers and the final retrieval decision remain pending a credentialed
  scheduled run; local deterministic mode is available without credentials.
- Local diagnostic result: vector retrieval scored 35%/40% Hit@5 for the two profiles;
  lexical hybrid scored 30%/35%, so hybrid is not adopted based on this local signal.

## Phase 13 implementation evidence

- Locust now supports authenticated `/ask` load tests through `KNOWLEDGEFORGE_TOKEN`.
- Added `scripts/backup_restore_check.py` for local pg_dump/pg_restore validation with
  document, chunk, and embedding integrity checks.
- Added worker-crash and malformed-delivery drill tests and documented retry behavior.
- Added `docs/phase13-operations.md` with repeatable performance and recovery commands.

Execution remains pending for the hosted-load and database-client portions; the local
Docker stack is now validated account-free.

## Phase 14 implementation evidence

- Added hard-delete account/document routes, raw-object deletion support, tenant-scoped
  failed-ingestion records, and migration 007.
- Added upload-size enforcement and OpenAPI coverage for lifecycle routes.
- Added privacy policy, terms of service, and support-path content.
- Added an oversized-upload adversarial test and retained prompt-injection/auth/tenant
  isolation coverage.
- Hosted security scan execution and human onboarding review remain pending.

## Phase 15 preparation evidence

- Added `docs/phase15-deployment-gate.md` with required inputs, execution order, and
  smoke, backup, dead-letter, and monitoring checks.
- Confirmed deployment workflow, Terraform configuration, and staging preflight are
  present for the eventual authenticated run.
- No GCP project, billing account, user, credential, secret, or deployment was created.

## Account-free completion roadmap

`docs/local-completion-roadmap.md` now defines the execution order and evidence gates
for full-stack emulators, database recovery, performance, evaluation, security, trust,
and onboarding work that can proceed without GCP.

## Roadmap start evidence â€” 2026-08-24

Completed in the current workspace:

- Ruff lint: passed.
- Ruff formatting: passed.
- mypy: passed.
- pytest: passed with the expected environment-dependent integration skip.
- Terraform formatting and validation: passed.
- Docker Compose and GitHub Actions YAML parsing: passed.
- Python bytecode compilation for source, scripts, and evaluation code: passed.
- Local deterministic Phase 12 evaluation: passed.

Environment gaps identified:

- Docker Desktop is installed and the local engine is available; Podman and nerdctl are
  not installed.
- `pg_dump` and `pg_restore` are not installed.
- Locust and Trivy are not installed.
- `uv` is not installed; the bundled workspace Python was used for checks.

The full emulator CI job, `scripts/run_full_local_stack.ps1`, and
`scripts/run_security_checks.ps1` are ready for repeat execution. PostgreSQL client
tools, Locust, and Trivy remain optional local gaps.

Workstream B implementation started:

- The emulator smoke harness now checks duplicate-upload idempotency.
- The emulator smoke harness now deletes the ready document and verifies a subsequent
  status lookup returns 404.
- The API now returns 404 when a caller attempts to delete a document outside its tenant
  or a document that does not exist.
- Static checks and the full local test suite remain passing after these changes.

Workstream A/B local Docker execution â€” 2026-08-24:

- Docker Desktop engine: client/server 29.7.2, healthy.
- Full Compose stack built successfully with PostgreSQL/pgvector, Redis, fake GCS,
  Pub/Sub emulator, migrations, API, worker, and smoke services.
- Local cloud initialization now uses the emulator REST API and exits successfully;
  it does not require gcloud credentials or a GCP project.
- Migration runner now tracks applied files and baselines an existing local schema,
  making stack restarts idempotent without deleting the database volume.
- End-to-end smoke passed: registration, asynchronous upload, duplicate-upload
  idempotency, worker processing to ready, document deletion, and post-delete 404.
- Extended lifecycle smoke passed: malformed Pub/Sub delivery reached the local
  dead-letter subscription after retry exhaustion; account deletion removed tenant data,
  invalidated tenant-scoped access, and deleted the exact raw fake-GCS object.
- A real referential-integrity defect found by the drill was fixed by deleting tenant
  documents explicitly before deleting the tenant row.

Account-free Phase 15 gate check on 2026-08-23:

- Terraform formatting: passed.
- Terraform validation: passed.
- Git diff validation: passed.
- `gcloud` executable: unavailable.
- GCP project, billing, credentials, and deployment: unavailable by design.

## Roadmap execution status â€” 2026-09-03 (R1â€“R7, F1â€“F8)

Everything code-writable in the roadmap is implemented. The session that completed
this stretch had no shell access (tool outage), so **no commands were re-run that
day** â€” every item below is implementation status plus static verification only.
Execution evidence comes from `scripts/r6_evidence.sh` / `.ps1` (see
`docs/launch-checklist.md` Â§1).

Implementation completed in this stretch (all with tests written alongside):

- R1 fixes: citation attribution, DELETE 404 semantics, limiter clock/fallback,
  worker idempotency claim.
- R2: retry/timeout/breaker wiring, boot-time secret validation.
- R3: eval harness corrections + 40-question golden set; quality-gate ratchet
  (`evaluation/check_thresholds.py`, floors in `evaluation/eval-thresholds.json`).
- R4: pooling, upload streaming guard, registration error semantics, mandatory
  tenant scoping, token/cost telemetry, batch upload quotas, structured logging,
  API-key/refresh-token auth, usage dashboard.
- R5: complete Terraform graph incl. push subscription with OIDC, dead-letter
  policy, monitoring-as-code (3 alert policies + SLO), plan-only CI workflow;
  the api/worker env dependency cycle is resolved via `local.worker_subscription`.
- R6 preparation: `scripts/r6_evidence.{sh,ps1}` (one-command evidence runner),
  `docker-compose.chaos.yml` + `scripts/emulator_chaos_test.py` (Redis-loss and
  redelivery-storm drills, wired as a CI job), `scripts/locustfile.py` load
  profile, and `LOCAL_GENERATION` mode so `/ask` runs end-to-end on the emulator
  stack (smoke test now asserts a cited answer, plain + SSE).
- R7: free-tier deploy scripts (`scripts/deploy-cloudrun.*`, `pull-and-deploy.*`).
- F1: conversations, follow-up rewriting, `/ask/stream` SSE.
- F2â€“F5: embedding cache, doc_type filter, chunk preview, reingest,
  .txt/.html ingestion, refresh-token rotation with replay detection, API keys.
- F6: eval gate + coverage ratchet (`.coverage-floor`, CI steps), chaos drills.
- F7: HNSW deliberately deferred â€” measure first (decision in `docs/decisions.md`).
- F8: trust documents remain drafts; legal-review checklist in
  `docs/launch-checklist.md` Â§6.

Known execution gates (see `docs/launch-checklist.md` for the full list):

- uv.lock is generated (2026-09-04, 96 packages resolved) but not yet committed;
  commit it before the next image build so CI and production run identical sets.
- `fake-gcs-server` is still `:latest` in `docker-compose.full.yml`; the digest-pin
  procedure is documented above the service definition.
- Coverage floor and eval floors are at their zero baselines until the first real
  runs; raise them with the `--update` flags and commit.
- The final `ruff`/`mypy`/`pytest` pass for this stretch has not been executed
  (shell outage); run `scripts/r6_evidence.sh` or the CI pipeline to produce it.

## 2026-09-04 - Phase 2.5 structured extraction (local evidence)

Locally validated, zero cloud credentials:

- Migration 014 applies cleanly alongside 001-013 against fresh PostgreSQL
  (all four new tables + documents columns verified; applied inside the
  emulator Postgres).
- PostgreSQL integration suite (8 tests) passes against a real database:
  tenant-scoped uniqueness, reprocess replacement, active-job uniqueness,
  sync-path ineligibility, claim/outbox lifecycle, allow-listed field filters,
  tenant isolation, document-deletion cascades (extractions, failures, jobs,
  outbox rows).
- Unit suites: 21 extraction pipeline/schema/classifier tests + 10 OCR/eval
  tests + 10 extraction API route tests, all passing.
- Full emulator stack (API, ingestion worker, extraction worker, outbox
  dispatcher) builds and the extended lifecycle smoke test passes end-to-end:
  upload invoice -> outbox dispatch -> extraction worker -> extraction row ->
  reprocess 202 -> job succeeded -> structured-filter ask with
  `[doc N, extracted fields]` citation -> document deletion cascades the
  extraction row. Extraction worker logs show job start/success per job.
- `check_extraction_accuracy.py` runs against the golden-set template with
  deterministic normalization (ISO dates, numeric totals, casefolds); the
  floor stays at the 0.0 baseline until the first real Gemini run.
- Bugs found and fixed while validating the rebuilt stack: `_read_upload`
  dropped all upload bytes (missing `parts.append`), plain-list embeddings
  fail pgvector 0.5's `<=>` operator (now wrapped in `Vector`), nullable
  filter params hit indeterminate-type errors under the new psycopg (filters
  now built as present-only clauses), and SSE `done` events failed on UUID
  serialization (`model_dump(mode="json")`).

Credential-gated (wired, waiting): real Gemini classification/extraction
against the golden set, free-tier Cloud Run deployment via
`scripts/deploy-cloudrun.{sh,ps1}` including the extraction service, outbox
Cloud Run Job + Scheduler trigger, and live `deploy_smoke_test.py`.
