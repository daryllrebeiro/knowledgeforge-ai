# KnowledgeForge AI — Pending Completion and Gaps

Updated: 2026-09-03

## Executive status

KnowledgeForge AI is substantially implemented through the local/code stages of Phases
0–15. The application, worker, persistence model, evaluation tooling, security controls,
local emulators, operational scripts, Terraform configuration, and CI workflows exist.

The remaining gaps are primarily execution evidence against real local containers,
credential-gated Gemini validation, and GCP deployment. No GCP project, billing account,
user account, secret, or deployment has been attached.

## Completed and locally validated

### Application capabilities

- FastAPI API with OpenAPI documentation.
- Legacy routes and `/v1` versioned routes.
- JWT authentication and bcrypt password hashing.
- Tenant-scoped document storage and retrieval (tenant scoping is mandatory in
  `retrieve_chunks`; the unfiltered branch was removed in R4.4).
- PDF, DOCX, Markdown, HTML, and plain-text extraction.
- Deterministic chunking with page metadata.
- Gemini embedding and answer-generation adapters; streaming generation (SSE).
- Follow-up question rewriting for conversations, best-effort with fallback.
- Grounded prompts with citation extraction and refusal behavior.
- Synchronous and asynchronous ingestion paths, re-ingestion of ready/failed
  documents, and an embedding cache keyed by model+content hash.
- Cloud Storage and Pub/Sub adapters.
- Local deterministic embeddings and answers for emulator-only operation.
- Conversations with persisted citations, refresh-token rotation with replay
  detection (family revocation), and API-key authentication.
- Pull-based local Pub/Sub worker.
- Retries, provider timeouts, and a Gemini circuit breaker wired into the
  embedding, generation, storage, and publish paths (`reliability.py`).
- Duplicate-message idempotency handling (atomic claim with a 10-minute lease;
  see `store.claim_document`).
- Request IDs, structured logs, usage logging (input/output tokens and cost
  estimate written by the ask and worker paths since R4.5), quotas, and rate
  limiting.
- In-memory and Redis-backed rate limiting (epoch-clock refill, graceful
  per-process fallback on Redis outage).
- Deny-by-default CORS and security response headers.
- Upload-size enforcement.
- Hard-delete document and account workflows.
- Tenant-scoped failed-ingestion records.

### Local tooling and infrastructure

- Terraform definitions for Cloud Run, Cloud SQL, Storage, Pub/Sub, Secret
  Manager, and monitoring (three alert policies, an email notification channel,
  and an ask-availability SLO — `infrastructure/terraform/main.tf` is the source
  of truth; `infrastructure/monitoring/slo.yaml` is superseded and kept only as
  a reference). The worker receives Pub/Sub push deliveries with OIDC, with a
  dead-letter topic and policy attached.
- One-command free-tier deployment scripts (`scripts/deploy-cloudrun.*`,
  `scripts/pull-and-deploy.*`) that provision a working environment end to end,
  independent of Terraform.
- Standard Docker Compose PostgreSQL/Redis stack.
- Full emulator Compose stack with PostgreSQL, Redis, fake-gcs-server, Pub/Sub emulator,
  API, worker, migrations, and initialization services.
- Local async smoke-test harness.
- Local PostgreSQL backup/restore integrity script.
- Locust load profile (`scripts/locustfile.py`) covering register, ask, upload,
  and document listing; it runs against the local stack (LOCAL_GENERATION) or a
  deployed environment.
- Migration runner for migrations 001–013.
- One-command R6 evidence runner (`scripts/r6_evidence.{sh,ps1}`) executing the
  full local checklist and teeing output to `docs/evidence/`.
- Chaos drill stack (`docker-compose.chaos.yml` +
  `scripts/emulator_chaos_test.py`): Redis loss and Pub/Sub redelivery storm,
  wired as a CI job.

### Quality and security tooling

- Unit and integration test tiers.
- Scheduled live-Gemini workflow definition.
- Ruff linting and formatting.
- mypy type checking.
- pytest suite.
- pip-audit workflow.
- CodeQL workflow.
- Trivy filesystem security scan workflow.
- Prompt-injection prompt construction tests (the unit test asserts hostile
  text is framed as data; **behavioral** injection testing against a real model
  is pending live-Gemini validation), tenant-isolation, malformed-delivery,
  worker-failure, and oversized-upload tests.

## Validation currently passing

- Ruff: passing.
- Ruff formatting: passing.
- mypy: passing.
- Local pytest suite: passing.
- Terraform formatting: passing.
- Terraform validation: passing.
- Compose YAML parsing: passing.
- Git diff validation: passing.

## Pending local execution evidence

These are implemented but not yet executed in the current workspace (a shell/tool
outage blocked command execution during the final stretch). One command produces
all of the evidence below: `scripts/r6_evidence.sh` / `scripts/r6_evidence.ps1`
(see `docs/launch-checklist.md` §1 for the exact invocation).

### Phase 10

- Run real PostgreSQL/pgvector tenant-isolation CI job.
- Run real Redis multi-instance rate-limit CI job.
- Record hosted CI migration execution.

### Phase 11

- Execute the full emulator stack.
- Verify register → upload → fake GCS → Pub/Sub → worker → PostgreSQL → ready.
- Republish the same message and verify no duplicate chunks.
- Publish a malformed message and verify retry/dead-letter routing.

### Phase 13

- Run Locust against the full local stack.
- Record P95 latency, error rate, and local concurrency ceiling.
- Execute PostgreSQL backup/restore and verify counts plus embeddings.
- Complete the migration recovery drill.

### Phase 14

- Validate account and document deletion against real local PostgreSQL and storage
  emulator data.
- Execute pip-audit and Trivy and record findings.
- Complete a human onboarding walkthrough and record friction points.
- Review trust documents for the intended operating jurisdiction.

## Pending Gemini validation

The following cannot be completed without a valid `GEMINI_API_KEY`:

- Gemini smoke test.
- Three-document live evaluation.
- Phase 12 20-source evaluation.
- Real Hit@5 and correctness measurements.
- Final hybrid-search/reranking decision based on real embeddings.

Local deterministic evaluation is available for development, but its results are not
production-quality evidence. Current local diagnostic results are:

| Profile | Vector Hit@5 | Hybrid Hit@5 |
|---|---:|---:|
| 500/100 | 35% | 30% |
| 800/150 | 40% | 35% |

The local evidence does not justify enabling hybrid ranking in production.

## Pending GCP deployment and managed-service validation

Phase 15 requires all of the following:

- GCP project with billing enabled.
- Authenticated `gcloud` or GitHub Workload Identity.
- Cloud Run, Cloud SQL Admin, Storage, Pub/Sub, Secret Manager, and Monitoring APIs.
- Artifact Registry location.
- Gemini API key.
- Database password.
- JWT secret.
- Approved region and staging environment.

After those prerequisites exist, the remaining execution sequence is:

1. Run `scripts/staging_preflight.ps1`.
2. Run and review Terraform plan.
3. Apply Terraform to staging.
4. Apply migrations 001–008 to Cloud SQL.
5. Deploy API and worker images.
6. Verify Secret Manager access.
7. Run authenticated registration, upload, status, ask, duplicate, delete, and account-
   deletion smoke tests.
8. Verify worker failure, failed status, retry, and dead-letter delivery.
9. Run backup restore and load tests.
10. Verify Cloud Monitoring alerts.
11. Promote through the production approval gate.

## Known design limitations

- Real Cloud SQL connectivity, connection pooling, and pgvector performance are not
  validated.
- Cloud IAM, quotas, naming collisions, managed-service latency, and billing behavior
  are unverified.
- Live Redis deployment is not validated.
- Cloud Monitoring dashboards and alert delivery are not validated.
- Backup restore has only been implemented as a local procedure; the evidence run is
  pending.
- The current trust documents are drafts and require jurisdiction-specific review.
- Local deterministic embeddings and answers are intended only for emulator testing.
- HNSW/pgvector indexing is deliberately deferred until staging load numbers exist
  (F7; see `docs/decisions.md`).

## Authoritative references

- [Build plan](../Plan.md)
- [Local completion roadmap](local-completion-roadmap.md)
- [Feature explainer](feature-explainer.md)
- [Launch checklist](launch-checklist.md)
- [Validation status](validation-status.md)
- [Phase 15 deployment gate](phase15-deployment-gate.md)
- [Security hardening](security-hardening.md)
- [Phase 13 operations](phase13-operations.md)
- [Migration policy](migrations.md)
- [Privacy policy](privacy-policy.md)
- [Terms of service](terms-of-service.md)
- [Support path](support.md)

## Completion definition

The project can be called locally complete when the emulator stack, integration tests,
backup/restore, load test, security scans, onboarding review, and Gemini evaluation have
all produced recorded evidence.

The project can be called production-ready only after Phase 15 has completed against a
real GCP project with authenticated deployment, managed-service validation, restore
verification, monitoring verification, and production smoke testing.
