# KnowledgeForge AI — Local Completion Roadmap

Updated: 2026-08-24

## Purpose

This roadmap closes every meaningful gap that does not require a GCP project, billing,
or managed-service credentials. It converts the existing code, emulator stack, CI
definitions, evaluation tooling, security controls, and recovery procedures into
recorded evidence.

GCP deployment remains a separate final gate. Nothing below creates a cloud account or
attaches a credential.

Roadmap execution began on 2026-08-24. Static, Python, Terraform, YAML, deterministic
evaluation, and full local container smoke checks are passing.

## Prerequisites for local completion

| Capability | Required for | Acceptable setup |
|---|---|---|
| Docker Desktop or Podman Compose | Postgres, Redis, GCS/PubSub emulators, full-stack tests | Local container runtime |
| PostgreSQL client tools | Backup/restore drill | `pg_dump` and `pg_restore` on PATH or in a utility container |
| GitHub Actions | Hosted integration/security evidence | Existing repository CI |
| Gemini API key (optional, not GCP deployment) | Real embedding/evaluation evidence | CI secret only |

## Workstream A — Reproducible local stack

### Goal

Prove the entire asynchronous ingestion flow with no managed cloud dependency.

### Work

1. Start `docker-compose.full.yml` with PostgreSQL, Redis, fake GCS, Pub/Sub emulator,
   migrations, API, and pull worker.
2. Run the smoke client: register, login, Markdown upload, GCS write, Pub/Sub publish,
   worker processing, and ready-state polling.
3. Record the command output and service logs in `docs/validation-status.md`.
4. Add a CI job that runs the same compose stack and uploads logs on failure.

### Definition of done

- The smoke test passes locally and in CI.
- No Gemini key or GCP credential is present in the stack.
- API, worker, storage, and database logs are retained as CI artifacts when a run fails.

### Status — local execution complete

Docker Desktop 29.7.2 built and ran the complete stack. The smoke test passed for
registration, asynchronous upload, worker readiness, duplicate-upload idempotency, and
document deletion. CI execution and failure-artifact review remain to be observed in the
hosted runner.

## Workstream B — Delivery failure, idempotency, and deletion

### Goal

Prove durable behavior across retries and lifecycle operations.

### Work

1. Publish one valid ingestion job twice and assert a single ready document with no
   duplicated chunks.
2. Publish a malformed job and assert retry exhaustion plus delivery to the emulator
   dead-letter subscription.
3. Delete a document and confirm its document row, chunks, embeddings, and raw emulator
   object are gone.
4. Delete an account and confirm tenant, user, documents, chunks, logs, failed-ingestion
   records, and raw objects are gone.

### Definition of done

- Each behavior has an integration test against the full emulator stack.
- Results and any emulator limitations are recorded in `docs/validation-status.md`.

### Status — complete locally

The expanded lifecycle smoke test passed duplicate delivery, malformed-delivery retry
exhaustion into the dead-letter subscription, document deletion, and full account
deletion including raw fake-GCS object cleanup. Hosted CI evidence remains separate.

## Workstream C — Database safety and recovery

### Goal

Prove that migrations and backup/recovery procedures work against real local Postgres.

### Work

1. Run migrations 001–007 on a clean pgvector database in CI.
2. Run two-tenant retrieval and shared Redis limiter tests against container services.
3. Populate local data, execute `pg_dump`, restore into a fresh database, and compare
   document/chunk counts plus at least one embedding.
4. Perform a forward-repair drill for one migration and document the exact procedure.

### Definition of done

- Migration order and fresh-install evidence exist.
- Restore integrity evidence is recorded.
- Recovery guidance in `docs/migrations.md` has been exercised, not merely described.

## Workstream D — Performance and resilience

### Goal

Measure local limits and make one evidence-based improvement.

### Work

1. Use a locally registered token to run Locust at stepped concurrency levels.
2. Record P50/P95/P99, request count, error rate, and the first degraded level.
3. Identify one bottleneck: connection usage, worker throughput, chunking, or embedding
   concurrency.
4. Apply a narrowly scoped improvement and repeat the same run.
5. Run the Gemini-outage circuit-breaker and worker-crash drills; reconcile the runbook
   with observed behavior.

### Definition of done

- Before/after measurements and one chosen bottleneck are in `docs/decisions.md`.
- The local SLO comparison is in `docs/validation-status.md`.
- The runbook reflects observed, not assumed, failure behavior.

## Workstream E — Evaluation quality

### Goal

Close the retrieval decision with reproducible data.

### Work

1. Keep the existing 20-source/20-question corpus versioned with source labels.
2. Run deterministic evaluation for regression detection on every relevant change.
3. When a Gemini key is available, run the credential-gated scheduled evaluation and
   upload the result artifact.
4. Compare baseline and large chunking profiles and inspect each miss.
5. Adopt hybrid/reranking only if the real measurement improves quality enough to justify
   latency and complexity; otherwise explicitly decline it with evidence.

### Definition of done

- Local regression results are committed or retained as CI artifacts.
- Real-Gemini Hit@5/correctness and the final retrieval decision are recorded when the
  optional key is supplied.

## Workstream F — Security, trust, and release readiness

### Goal

Turn defined controls into reviewed evidence.

### Work

1. Run `pip-audit`, CodeQL, and Trivy; record findings, fixes, or accepted risks.
2. Run adversarial tests: missing/invalid JWT, cross-tenant IDs, oversized upload,
   malformed document, injection-laden content, malformed Pub/Sub delivery, and account
   deletion.
3. Review OpenAPI as a first-time client and resolve inconsistent route descriptions or
   legacy/`/v1` differences.
4. Complete the human first-ten-minutes walkthrough and record friction points.
5. Obtain jurisdiction-specific review of privacy and terms drafts before accepting real
   users.

### Definition of done

- Security scan output is retained in CI or `docs/validation-status.md`.
- Adversarial test outcomes are recorded.
- Human onboarding findings are recorded and triaged.
- Legal drafts have an owner and review status.

## Recommended execution order

1. Install or make available a local container runtime.
2. Run Workstream A, then B and C in the same stack.
3. Run Workstream D after the stack is stable.
4. Run Workstream F in parallel with A–D.
5. Run Workstream E locally immediately; use Gemini evaluation only when a key is
   intentionally provided.
6. Update `docs/pending-completion-and-gaps.md` after each evidence-producing run.
7. Enter Phase 15 only after these account-free evidence gates are closed.

## GCP-only boundary

The following deliberately remain out of scope until a GCP project is supplied:

- IAM and Workload Identity validation
- API enablement, quota, billing, and naming-collision checks
- Cloud Run deployment and managed-service networking
- Secret Manager access verification
- Cloud SQL backup restore
- Cloud Monitoring dashboards and alert delivery
- Production smoke tests and promotion

See [Phase 15 deployment gate](phase15-deployment-gate.md) for the later authenticated
execution sequence.
