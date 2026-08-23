# Validation status

## Local/CI-complete checks

- Phases 1–3: extraction, chunking, prompting, retrieval, evaluation harness, and API contract tests.
- Phase 4: JWT/password tests, protected-route tests, tenant propagation, and prompt-injection coverage.
- Phase 5: job idempotency, async worker pipeline code, status transitions, and cloud adapter configuration.
- Phase 6: structured request logging, request-log persistence, retry/circuit-breaker tests, and SLO/runbook artifacts.
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

### Task 1 — GCP project and API provisioning

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

Execution remains pending because Docker and PostgreSQL client tools are unavailable in
the current workspace.

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

Account-free Phase 15 gate check on 2026-08-23:

- Terraform formatting: passed.
- Terraform validation: passed.
- Git diff validation: passed.
- `gcloud` executable: unavailable.
- GCP project, billing, credentials, and deployment: unavailable by design.
