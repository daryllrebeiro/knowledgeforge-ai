# Launch Checklist

Updated: 2026-09-03

Everything code-writable through R6 is implemented. What remains is **execution
evidence**, which is gated on (a) Docker/PostgreSQL locally, (b) a GCP project
with credentials for deployment, and (c) legal review for the trust documents.
This checklist is the single place to track those gates; each item names the
command to run and where the evidence goes (`docs/validation-status.md`).

## 1. Local execution evidence (R6) — gated on Docker + local PostgreSQL

One command produces all of it:

```
DATABASE_URL=postgresql://knowledgeforge:knowledgeforge@localhost:5432/knowledgeforge \
RESTORE_DATABASE_URL=postgresql://knowledgeforge:knowledgeforge@localhost:5432/knowledgeforge_restore \
R6_LOAD_RUN=1 ./scripts/r6_evidence.sh        # or scripts/r6_evidence.ps1
```

- [ ] quality-gates: `ruff check`, `ruff format --check`, `mypy src` all pass.
- [ ] unit-tests: full non-integration suite passes; coverage ratchet green.
- [ ] integration-tests: real-PostgreSQL suite passes (migrations applied first).
- [ ] emulator-smoke: register → upload → worker → `ready` → `/ask` with cited
      answer (plain + SSE) → duplicate-upload idempotency → dead-letter routing
      → document/account deletion, all against the emulator stack.
- [ ] load-run: Locust `/ask` P95 latency, error rate, and concurrency numbers
      recorded (local mode = no Gemini; repeat against staging for real
      provider latency).
- [ ] chaos-drills: Redis loss (auth/upload/ask still succeed, degraded
      limiting) and Pub/Sub redelivery storm (all messages dead-lettered,
      valid traffic unaffected).
- [ ] backup-restore: `pg_dump`/`pg_restore` integrity check passes.
- [ ] Paste the summary + relevant numbers into `docs/validation-status.md`;
      empty the "Pending local execution evidence" section of
      `docs/pending-completion-and-gaps.md`.

## 2. Repo hygiene before any deploy — gated on network/tool access

- [ ] Generate and commit `uv.lock` (`uv lock`): both Dockerfiles use
      `uv sync --frozen`, so **no image can build without it**.
- [ ] Pin the `fake-gcs-server` image to an immutable digest in
      `docker-compose.full.yml`:
      `docker buildx imagetools inspect fsouza/fake-gcs-server:latest` →
      replace `:latest` with `@sha256:<digest>` (R5.8).
- [ ] `terraform fmt -check -recursive infrastructure/terraform` and
      `terraform init -backend=false && terraform validate` pass locally (CI
      runs both on every PR).
- [ ] Raise the quality floors from their zero baselines after the first real
      runs: `uv run python scripts/coverage_ratchet.py coverage.json --update`
      and `uv run python evaluation/check_thresholds.py eval-report.json --update`.
      Commit the raised `.coverage-floor` / `evaluation/eval-thresholds.json`.

## 3. R7 deployment execution — GATE: GCP project + `gcloud` login

- [ ] Provision (or pick) a GCP project with billing enabled; enable the APIs
      listed in `scripts/deploy-cloudrun.sh`.
- [ ] Provide `DATABASE_URL` (free-tier pgvector host, e.g. Neon/Supabase) and
      a real `GEMINI_API_KEY`.
- [ ] Run `scripts/staging_preflight.ps1` (or `.sh`); record output.
- [ ] Run `scripts/pull-and-deploy.ps1` end to end; record output.
- [ ] On the deployed stack verify: register → upload (async path) → Pub/Sub
      push with OIDC → worker → `ready` → `/ask` with citations; duplicate
      upload idempotent; delete document/account cleans storage.
- [ ] Verify a malformed delivery reaches the dead-letter topic (check
      subscription metrics or logs).

Phase 2.5 additions:

- [ ] Verify the extraction Cloud Run service, outbox Cloud Run Job, and
      Cloud Scheduler trigger deployed with dedicated service accounts
      (script stage 7 output).
- [ ] Upload an invoice → outbox dispatch (≤2 min) → extraction row via
      `GET /documents/{id}/extraction`; `POST /ask` with
      `structured_filters` cites `[doc N, extracted fields]`.
- [ ] `POST /documents/{id}/extraction/reprocess` returns 202 and
      `GET /extraction-jobs/{id}` reaches `succeeded`.
- [ ] Confirm extraction token usage appears in `/admin/usage`
      (same metering path).
- [ ] Malformed `document.ready` message reaches the extraction dead-letter
      topic; a valid extraction still completes afterward.

## 4. Terraform path (alternative to R7 for managed services) — GATE: same

- [ ] `terraform init` with a real (or local) backend; `terraform plan` on a
      scratch project shows a complete, connected, cycle-free graph.
- [ ] Human review of the plan (CI's plan job is review-only by design).
- [ ] `terraform apply` to staging — first real execution of the R5 graph.
- [ ] Confirm monitoring resources: 3 alert policies, email channel, and the
      ask-availability SLO appear in Cloud Monitoring.
- [ ] Confirm the Phase 2.5 resources: extraction topic/subscription with
      dead-letter policy, extraction service, outbox job, scheduler job,
      and their IAM bindings.

## 5. Staging validation (Phase 15) — GATE: deployed environment from §3 or §4

- [ ] Apply all migrations (001–014) to the managed database; verify order and
      idempotency (re-run is a no-op).
- [ ] Verify Secret Manager access from both services (boot-time
      `validate_runtime` passing is the proof).
- [ ] Authenticated smoke suite: registration, login, refresh rotation, logout
      family revocation, API-key auth, upload PDF/DOCX/MD/TXT/HTML, reingest,
      chunk preview, conversations + streaming ask, usage dashboard.
- [ ] Worker failure drill: break the storage path, confirm document →
      `failed`, retries, then dead-letter after 5 attempts.
- [ ] Backup/restore against the managed database; record restore integrity.
- [ ] Load test against staging (`scripts/locustfile.py`) with real Gemini:
      record P95 `/ask`, error rate, breaker behavior under provider errors.
- [ ] Verify alerts fire (e.g. dead-letter depth > 0 during the failure drill)
      and the SLO dashboard populates.
- [ ] Record everything in `docs/validation-status.md`; only then promote past
      the production approval gate (`docs/phase15-deployment-gate.md`).

## 6. F8 legal review — GATE: jurisdiction-specific counsel

- [ ] `docs/privacy-policy.md`, `docs/terms-of-service.md`, `docs/support.md`
      reviewed for the operating jurisdiction (currently drafts).
- [ ] Data-retention and deletion posture confirmed legal (hard deletes +
      storage cleanup are implemented; retention windows are not).
- [ ] If EU users: DPA, subprocessor list (Google Cloud/Gemini), and transfer
      safeguards documented.

## 7. F7 (HNSW/pgvector scaling) — deferred by decision

Deliberately **not** built: measure first. HNSW indexes only make sense after
real staging load numbers exist (§5). Revisit once the load test shows vector
search dominating P95; record the decision in `docs/decisions.md` either way.
