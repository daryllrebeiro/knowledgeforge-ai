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
