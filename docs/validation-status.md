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
