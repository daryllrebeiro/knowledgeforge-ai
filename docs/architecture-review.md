# KnowledgeForge AI — Senior Architecture Review

**Reviewer:** Architecture review
**Date:** 2026-09-03
**Scope:** Entire repository — application source, tests, CI, infrastructure, evaluation tooling, documentation, and the build plan (`Plan.md`).
**Verdict up front:** A well-organized, honest, and disciplined codebase with genuinely good CI design and clean security fundamentals — but it is **not yet production-ready**, and several documented claims do not match the code. There are five correctness bugs, one non-functional deployment definition, and a set of "implemented" reliability features that exist as dead code. None of these are hard to fix; all of them matter before real users arrive.

---

## 1. Executive summary

KnowledgeForge AI is a multi-tenant RAG platform (FastAPI + Gemini + pgvector) with ~1,500 lines of application source, ~550 lines of tests, seven forward-only SQL migrations, Terraform for GCP, a five-tier CI pipeline, and an unusually candid set of status documents.

**What is genuinely good:**

- **Clean layered architecture.** `ingestion` / `retrieval` / `generation` / `security` / `worker` separation matches the plan. Dependencies point the right way. The `TextGenerator` Protocol makes generation testable without Gemini.
- **SQL discipline.** Every query is parameterized. Tenant scoping is enforced in `WHERE` clauses, not application filtering — and it is proven against a real pgvector container in CI (`tests/integration/test_postgres_tenant_isolation.py`).
- **CI tiers are real, not theater.** Unit, real Postgres+Redis integration, a full emulator stack (fake-gcs + Pub/Sub emulator + worker + smoke test), credential-gated live-Gemini eval, and Terraform fmt/validate (`/.github/workflows/ci.yml`).
- **Security basics are present and mostly correct:** bcrypt hashing, JWT with fixed algorithm, deny-by-default CORS, security headers (`observability.py:46-49`), auth rate limiting keyed by caller IP, upload size caps, document quotas, tenant-scoped failed-ingestion records, GCS URI prefix validation in the worker (`worker/cloud.py:23-26`).
- **Honest self-reporting.** `docs/pending-completion-and-gaps.md` and `Plan.md`'s status checkpoint are unusually truthful about what has *not* been executed against real infrastructure.

**What is not good (detailed below):**

- Five correctness bugs, two of which produce wrong user-visible results (citations and document deletion).
- The Terraform definition cannot produce a working deployment: missing runtime env vars, no Pub/Sub→worker wiring, no IAM.
- Retry, timeout, and circuit-breaker code exists but is **never called** — despite being claimed as delivered in three separate documents.
- The Redis rate limiter uses a per-process clock for shared state, making it incorrect in exactly the multi-instance scenario it exists for.
- The golden set is still a 2-question fixture; the only measured Hit@5 (35–40%) comes from non-semantic hash embeddings, so **retrieval quality is currently unmeasured**.
- Test depth is thin (32 tests) relative to the 15 phases claimed, and the two Phase 4 Definition-of-Done security tests are placeholders in substance.

---

## 2. Findings

Severity: **Critical** (wrong behavior or security exposure in the default configuration) · **High** (broken under realistic conditions or blocks production) · **Medium** (quality/perf/maintainability) · **Low** (polish).

### 2.1 Critical

#### C1. Citation attribution matches on page number across documents — wrong `document_id` in citations
`src/knowledgeforge/api.py:358-363`

```python
citations = [
    CitationResponse(document_id=document_id, page=citation.page)
    for citation in answer.citations
    for document_id, chunk in retrieved
    if chunk.page == citation.page
]
```

Every retrieved chunk that shares a page number with a cited page is emitted as a citation. If Tenant's corpus contains two documents that both have a page 4, a citation for page 4 fans out to **both** document IDs. Consequences:

- Users see citations pointing at documents that did not support the answer.
- **The evaluation harness inherits the bug.** `evaluation/run_eval.py:19-20` computes Hit@5 from exactly these citations, so measured Hit@5 is inflated — a "hit" can come from a colliding page number rather than the right document.

The root cause is upstream: the prompt (`generation/prompt.py:13-15`) identifies chunks only as `[page N]`, with no document identity, so the model *cannot* cite a specific document. Fix the prompt (per-chunk labels like `[doc 2, page 4]` referencing a numbered document list), then map citations back through the retrieved set by chunk identity rather than by page.

#### C2. `DELETE /documents/{id}` returns 404 while actually deleting the document
`src/knowledgeforge/ingestion/store.py:190-198` + `src/knowledgeforge/api.py:393-395`

```python
cursor.execute(
    "DELETE FROM documents WHERE id = %s AND tenant_id = %s RETURNING storage_uri", ...
)
...
return None if row is None else row[0]      # row[0] is NULL for sync-ingested docs
```

`storage_uri` is `NULL` for every document ingested through the synchronous path (the default; `ASYNC_INGESTION=false`). The delete commits, `RETURNING` yields a row whose single column is `NULL`, `delete_document` returns `None`, and the API maps `None` to 404 "Document not found." The tenant sees a failure; the document is gone. The two-`None` meanings ("no row" vs. "no storage URI") must be distinguished — e.g. `RETURNING id, storage_uri` and branch on row presence.

#### C3. The Terraform stack cannot deploy a working system
`infrastructure/terraform/main.tf`

- **API service (lines 56–85) sets `ASYNC_INGESTION`, `GCS_BUCKET`, `PUBSUB_TOPIC`, `GEMINI_API_KEY` — but not `DATABASE_URL`, not `JWT_SECRET_KEY`, not `REDIS_URL`.** The API boots with the `config.py:15` default (`localhost:5432`) and the default JWT secret. Every database call fails; every token is signed with `"change-me-in-production"`.
- **Worker service (lines 87–94) has no environment at all.** No `DATABASE_URL`, no `GCS_BUCKET`, no `PUBSUB_SUBSCRIPTION`. The worker cannot connect to anything.
- **Pub/Sub is not wired to the worker.** The subscription (lines 18–25) has a dead-letter policy but no `push_config`, and there is no IAM binding granting the Pub/Sub service account permission to invoke the worker. The push entrypoint (`worker/entrypoint.py`) expects push delivery that is never configured; the worker image runs uvicorn on 8080 but nothing calls it.
- **No service accounts, no Secret Manager accessor IAM, no `google_project_service` API enablement, no Cloud SQL user** (the `database_password` variable is declared and never used), no VPC/connector for the database, and public IPv4 on the SQL instance with no authorized networks.
- **No monitoring resources exist**, despite `docs/pending-completion-and-gaps.md:41-42` claiming "Terraform definitions for … monitoring resources." `infrastructure/monitoring/slo.yaml` is a standalone file nothing applies — and it has its own bugs (see M9).

`terraform validate` passes because the HCL is syntactically fine. This is the classic gap the plan's own Phase 8.5 gate anticipated; the review confirms it is not merely "unexecuted" but incomplete as a definition.

#### C4. Redis rate limiter uses a per-process clock for shared state
`src/knowledgeforge/limits.py:61`

```python
self._client.eval(self._SCRIPT, 1, key, monotonic(), capacity, ...)
```

`monotonic()` is undefined across processes and machines — it is time since an arbitrary, per-boot origin. The Lua script computes `tokens + (now - updated) * refill` where `now` and `updated` come from *different replicas*. Deltas are arbitrary garbage (positive, or enormously negative), so the shared limiter either never refills or refills instantly. This is precisely the horizontal-scaling scenario the Redis limiter exists for (`docker-compose.full.yml`, Cloud Run `max_instance_count = 3`). Use epoch time (`time.time()`) — the Lua script's atomicity, not clock secrecy, is what matters here.

#### C5. No runtime fallback when Redis is down — every protected endpoint 500s
`src/knowledgeforge/limits.py:70-79`

`build_limiter` only falls back to the in-memory limiter when `redis_url` is empty or the package is missing. Once the Redis client is chosen, a Redis outage at request time raises an unhandled `redis.RedisError` out of `limiter.check` → 500 on `/ask`, `/documents`, `/auth/*`. `Plan.md` Phase 9 claims "Redis-backed shared rate limiting implementation with local fallback" — the fallback does not exist at runtime. Catch connection errors in `check` and degrade to the local limiter (and log it loudly).

---

### 2.2 High

#### H1. Retry / timeout / circuit-breaker code is dead — and claimed as delivered
`src/knowledgeforge/reliability.py` defines `with_retry` (tenacity) and `CircuitBreaker`. **Nothing in the codebase calls them** (grep: only `reliability.py` and its unit test reference them). `_gemini_client()` / `GeminiTextGenerator.generate` (`generation/gemini.py:9-13`), `embed_texts` (`ingestion/embed.py`), and the GCS/PubSub adapters (`worker/cloud.py`) have no timeout, no retry, no breaker. A hung Gemini call hangs `/ask` until the client gives up.

This is contradicted by three documents: `Plan.md` Phase 6 ("Timeout + single retry … on every external call", "Circuit breaker on the Gemini call"), `docs/validation-status.md:8` ("retry/circuit-breaker tests"), and `docs/pending-completion-and-gaps.md:32` ("retries, and circuit breakers"). The *tests* for the dead code exist; the wiring does not. This is the single largest claim-vs-reality gap in the project.

#### H2. Worker idempotency is a status check with a race — chunk duplication is possible
`src/knowledgeforge/ingestion/jobs.py:13-15`, `src/knowledgeforge/worker/pipeline.py:35-39`

- `should_process` admits statuses `{pending, failed}`. Two concurrent deliveries of the same message (at-least-once redelivery while the first is mid-flight) both pass the check and both run the pipeline.
- In `process_ingestion_job`, the `DELETE FROM chunks` and the subsequent `store_chunks` insert run in **separate transactions** on the same connection — a crash between them leaves a `pending` document with zero chunks (recoverable), but interleaved concurrent runs can double-insert.
- There is no unique constraint on `chunks` and no content-hash guard at insert time. `Plan.md` Phase 5 specifies "dedupe on `content_hash` + `document_id`" — the hash is carried in the job message and never used for dedup.

Fix options: `SELECT … FOR UPDATE` / status `UPDATE … WHERE status = 'pending'` as an atomic claim before processing, or a unique `(document_id, content_hash)`-derived constraint. Then prove it with the duplicate-delivery test the plan already specifies (Phase 11, unchecked).

#### H3. Token/cost telemetry is never written — `/admin/usage` cost is always 0
`src/knowledgeforge/ingestion/store.py:216-233` inserts only `request_id, tenant_id, query, retrieved_chunk_ids, latency_ms`. Migration 006 defines `input_tokens`, `output_tokens`, `cost_estimate` — nothing ever populates them. `tenant_usage` (`store.py:246-251`) sums a column that is always NULL → `/admin/usage` reports `cost_estimate: 0.0` forever. Phase 6/7 claims "cost_estimate populated on every `/ask` call" and "Cost-per-tenant is queryable" — the plumbing exists, the data does not. The Gemini SDK responses carry usage metadata; capture and persist it.

Related: `api.py:370` writes **document IDs** into `request_logs.retrieved_chunk_ids` (`retrieve_chunks` returns `(document_id, TextChunk)` — `src/knowledgeforge/retrieval/retrieve.py:17`). The column name is wrong for what it stores, which will mislead anyone debugging retrieval from logs.

#### H4. Retrieval quality is unmeasured; the golden set is a placeholder; the eval inherits bug C1
- `evaluation/golden-set.json` is the 2-question fixture with `expected_document_id: "fixture-document"` — exactly what `Plan.md:366-368` admits.
- The only recorded numbers (Hit@5 35–40%, `docs/decisions.md:80-98`) come from `embed_texts_local` — deterministic SHA-256 hash vectors with no semantic content. They measure nothing about production retrieval.
- `run_eval.py` derives Hit@5 from citations produced by the C1 page-collision bug, and never uses `expected_answer_summary` — there is no answer-correctness metric at all.
- `section_aware` chunking (`ingestion/chunk.py:16`) and the `doc_type` retrieval filter (`retrieval/retrieve.py:15`) — both Phase 3 commitments — are implemented but unreachable: no caller passes either parameter, and no API surface exposes the filter. The Phase 3 experiments have not actually run against real embeddings.

Until a real Gemini-keyed eval produces numbers, the core product property — retrieval quality — is unvalidated. This should be the top priority after the correctness fixes.

#### H5. JWT secret has no fail-closed behavior
`src/knowledgeforge/config.py:18` defaults `jwt_secret_key` to `"change-me-in-production"`; `.env.example:21` ships it; nothing validates it at boot. If the environment variable is forgotten in any deployment (see C3 — Terraform doesn't set it), tokens are forgeable by anyone who reads this public repository. Add a startup check: refuse to serve non-`/health` traffic when `environment != "development"` and the secret is a known default or under a minimum entropy length. Same treatment for the Gemini placeholder key (which *is* checked at request time in `api.py:174` — apply that pattern to the secret at boot).

#### H6. ivfflat index built on an empty table
`migrations/001_initial.sql` creates `ivfflat (embedding vector_cosine_ops) WITH (lists = 100)` before any data exists. IVF centroids are computed from the indexed rows at build time; an index built on an empty table has degenerate centroids and poor recall/latency until reindexed. Standard practice: build (or `REINDEX`) after the first bulk load, and re-evaluate `lists` against actual corpus size; consider HNSW for the filtered multi-tenant access pattern. Add a documented post-load reindex step to the ingestion/ops runbook.

---

### 2.3 Medium

#### M1. A new database connection per request, no pooling
Every endpoint does `psycopg.connect(settings.database_url)` inline (`api.py` uses it seven times in one file). Under load this is TCP+auth handshake per call, and on Cloud Run it is a recipe for Cloud SQL connection exhaustion. Adopt `psycopg_pool` (or the Cloud SQL Python Connector) with a pooled connection injected via FastAPI dependency.

#### M2. Memory-first upload handling
`api.py:187-189` reads the entire multipart body into memory (`file.file.read()`) *before* checking `max_upload_bytes`. A 2 GB body is fully read before rejection. Check `Content-Length` early and stream/chunk the read with a hard cutoff.

#### M3. Registration conflates every failure with 409
`api.py:147-148` catches *all* exceptions — DB unreachable, disk full — and returns 409 "Unable to register account". Duplicate email (the actual 409 case) and infrastructure failure are indistinguishable to callers and logs. Catch the unique-violation specifically; let infra errors surface as 503.

#### M4. The prompt-injection and cross-tenant tests don't test what the plan requires
- Phase 4 DoD: "ingest a document containing 'ignore previous instructions…'; assert the model treats it as inert text." `tests/unit/test_prompt.py:28-31` only asserts the hostile string *appears in the prompt*. No model behavior is tested anywhere.
- The only auth-surface test is `tests/unit/test_security_routes.py` (one route). No test asserts that *each* protected route rejects missing tokens; no CORS test; no test that the page-collision citation bug (C1) or delete-404 bug (C2) doesn't exist — both bugs would have been caught by straightforward endpoint tests with two documents.

#### M5. `retrieve_chunks` has an unscoped mode
`retrieval/retrieve.py:24-33` — when `tenant_id is None`, the query searches **all tenants**. Callers currently always pass it, but the function's contract invites a future cross-tenant leak. Make `tenant_id` required, or at minimum assert it is non-None outside tests.

#### M6. Batch upload quirks
`api.py:292-320` — the batch endpoint calls the single-file endpoint per file, so one batch consumes N rate-limit tokens and can hit the tenant document quota mid-batch; `record_failed_ingestion` failures are swallowed with `except Exception: pass` (`api.py:315-316`); and there is no per-file size accounting against the request body limit.

#### M7. Citation semantics differ by document type
For DOCX, "page" is a paragraph index (`extract_docx.py:12`); for Markdown, it is a token-stream index (`extract_markdown.py:15-17`). The UI contract says `page: int`, and the model is told to emit `[page N]`. For non-PDF documents this is misleading to users. Either generalize the citation field (`location`), or map paragraph/section indices to something the user can navigate to.

#### M8. Observability gaps
- `configure_logging` (`observability.py:17`) hardcodes INFO and ignores `settings.log_level`; it configures only the `knowledgeforge.api` logger — uvicorn access logs, worker logs, and third-party logs are unstructured, undercutting the "structured JSON logging" claim.
- The worker logs nothing: no job start/end, durations, or failure reasons (`worker/pipeline.py`, `worker/entrypoint.py`).
- `/ask` has no end-to-end timeout: a slow Gemini call can hold a worker thread indefinitely.

#### M9. Monitoring assets are inconsistent and unapplied
`infrastructure/monitoring/slo.yaml` — the SLO filter targets `service_name="knowledgeforge-api"` but Terraform names the service `knowledgeforge-api-${var.environment}` (never matches); the "dead-letter non-empty" alert watches `subscription/num_undelivered_messages` with **no subscription filter** (it alerts on the *live* subscription's backlog, not the dead-letter topic), and nothing in Terraform applies this file. No alert policies, dashboards, or uptime checks exist as code.

#### M10. Deployment pipeline runs `terraform apply -auto-approve` with no plan review
`.github/workflows/deploy.yml:37-39` — no `terraform plan` step, no artifact/PR-comment review; the "manual production gate" is implicit in GitHub environment protection rules that are not documented in the repo. Add an explicit plan step and document the required environment protection configuration.

#### M11. Container and dependency hygiene
- Both Dockerfiles run as **root**, have no `HEALTHCHECK`, and install via `pip install .` — resolving dependencies from `pyproject.toml` ranges at build time, while CI uses `uv sync` with the lockfile. CI and production can run different versions. Install from the lockfile (`uv sync --frozen` or export) and add a non-root `USER`.
- `docker-compose.full.yml` pins `fake-gcs-server:latest` (unpinned, non-reproducible emulator runs).
- `passlib 1.7` + `bcrypt>=4` is a known-irritation combination (version-detection warning; the constraint has been worked around but should be re-tested after any bcrypt bump).

#### M12. Small dead/unused code inventory
- `ingestion/dedup.py:decide_dedup` — only tests call it; `api.py` re-implements the logic inline. Either use the function or delete it.
- `reliability.py` entirely (see H1) until wired.
- `section_aware` / `doc_type` retrieval options (see H4) until exposed.
- `docs/decisions.md:80-98` contains the same "Local deterministic diagnostic run" table pasted twice.

#### M14. The deploy workflow's Cloud Build command uses a flag that doesn't exist
`.github/workflows/deploy.yml:28` — `gcloud builds submit --tag … -f infrastructure/Dockerfile.worker .` The `gcloud builds submit` command has no `-f`/dockerfile flag; selecting a non-root Dockerfile requires a `cloudbuild.yaml` config. This workflow would fail at the worker-image build step on its first real execution — one more "defined but never run" artifact. (The `scripts/deploy-cloudrun.*` scripts added alongside this review generate a cloudbuild config and build both images in one submission; the workflow should adopt the same approach.)

#### M13. Product-level API gaps for a "productization" phase
- **No `GET /documents` list endpoint** — users cannot discover what they uploaded, only poll a known ID.
- No pagination anywhere (`/ingestions/failed` is unbounded).
- No refresh tokens or logout/revocation; 60-minute access tokens only.
- One tenant per registration (email is globally unique, `users.email UNIQUE`), so "multi-tenancy" currently means "one user per tenant." Fine as an MVP decision — should be stated explicitly as one.
- `_gemini_client()` and `PubSubPublisher` are constructed per request (`api.py:172-176`, `api.py:229`) — cache them.

---

### 2.4 Low

- **L1.** `worker/pull_entrypoint.py:46` uses `if "job" in locals()` — fragile control flow; initialize `job = None` and check for None. Inline `import psycopg` inside the callback (lines 34, 47) is stylistically inconsistent.
- **L2.** The in-memory limiter's `_buckets` dict (`limits.py:10`) never evicts stale entries — unbounded growth keyed by (IP, purpose). Add a periodic sweep or LRU cap.
- **L3.** `Plan.md` specifies Alembic; the project uses raw SQL + `scripts/apply_migrations.py`. The forward-only policy is documented in `docs/migrations.md` — a legitimate decision, but the plan text should be reconciled.
- **L4.** `README.md` says `uv run pytest` runs "the checks" but doesn't mention markers; the Docker `EXPOSE 8000` has no container healthcheck while the compose file defines one at the service level.
- **L5.** Error message strings leak implementation details: `api.py:276` returns `f"Unable to ingest PDF: {exc}"` for DOCX/Markdown files too, and embeds raw exception text into API responses (minor information disclosure; also wrong copy).
- **L6.** `chunk_pages` cannot produce chunks spanning page boundaries — good for citation precision, but it silently truncates semantic units at page breaks for typical PDFs. Worth revisiting once a real eval exists (relates to H4).
- **L7.** `Plan.md` is 29 KB of mixed plan + status log. The status checkpoints inside it duplicate `docs/pending-completion-and-gaps.md`; consider moving status out of the plan document.
- **L8.** Worker push endpoint (`worker/entrypoint.py`) performs no OIDC token verification of the Pub/Sub caller. Acceptable *only if* Cloud Run ingress + invoker IAM is locked down — which C3 shows is not yet defined. Must be addressed with the Terraform work.

---

## 3. Documentation vs. reality audit

The project's greatest strength is its honesty about *execution* gaps ("nothing has run against real infrastructure"). The review found the honesty does not fully extend to *capability* claims:

| Claim (location) | Reality |
|---|---|
| "retries, and circuit breakers" delivered (`docs/pending-completion-and-gaps.md:32`, `Plan.md` Phase 6, `docs/validation-status.md:8`) | Code exists in `reliability.py`; never called by any production path (H1) |
| "Terraform definitions for … monitoring resources" (`docs/pending-completion-and-gaps.md:41-42`) | No monitoring resources in Terraform; `slo.yaml` unapplied and buggy (C3, M9) |
| "Duplicate-message idempotency handling" (`docs/pending-completion-and-gaps.md:31`) | Status-check only; race window; no hash/unique dedup (H2) |
| "Redis-backed … with local fallback" (`Plan.md:430`) | Fallback only when the package is missing, not when Redis is down (C5) |
| "cost_estimate … populated on every `/ask` call" (`Plan.md:270`) | Columns never written; cost is always 0 (H3) |
| "Prompt-injection … coverage" (`docs/validation-status.md:6`, `docs/pending-completion-and-gaps.md:61-62`) | Asserts hostile text appears in the prompt; no behavior test (M4) |
| "Deny-by-default CORS and security response headers" (`docs/pending-completion-and-gaps.md:34`) | **Accurate** — verified in `main.py:21-31`, `observability.py:46-49` |

**Recommendation:** after fixing, re-audit every "completed" checkbox in `Plan.md` against code references. The pattern of "test exists for module X" being reported as "module X is in production use" is how the reliability-code gap happened.

---

## 4. Domain-by-domain assessment

### Architecture — **good**
Layering, dependency direction, and the sync→async ingestion evolution are sound. The biggest structural criticism: the "service layer" lives in `api.py` route handlers (the upload handler is ~100 lines with two full ingestion strategies inline). Extract an application service layer so routes are thin, the sync/async paths share code, and the batch path stops calling route functions directly.

### Security — **fundamentals good, edges unguarded**
Parameterized SQL throughout, query-level tenant scoping, bcrypt, header hygiene, deny-by-default CORS: all verified. Unguarded edges: default JWT secret with no boot check (H5), no OIDC verification plan for the push worker (L8), unscoped retrieval mode (M5), 404-vs-403 information equality is fine, but registration error conflation (M3) and exception text in responses (L5) need cleanup.

### RAG quality — **unvalidated**
The pipeline is well-built (page-preserving chunks, batched embeddings, grounded prompt with an anti-injection clause, refusal contract). But chunking experiments, hybrid search, and reranking are all unexecuted against real embeddings; citations are ambiguous by design (C1); and there is no answer-correctness metric. This is the product's core value proposition and it is currently a hypothesis, not a measurement.

### Data model — **solid**
Migrations are readable, ordered, and idempotent via the runner; the tenant-scoped unique hash index (005) correctly fixed the earlier global-unique mistake; forward-only policy is documented. Gaps: empty-table ivfflat (H6), no constraints supporting worker idempotency (H2), unused telemetry columns (H3).

### Reliability/operations — **designed, not connected**
Retries, breakers, SLOs, runbook, dead-letter topic, backup script: all exist on paper. None of the runtime code paths use the reliability machinery; monitoring isn't applied; load tests and restores have never executed. `docs/runbook.md` exists — verify its steps against the real stack when Phase 13 runs.

### Testing — **right shape, thin depth**
32 tests. The tiering strategy (unit / real-service integration / emulator stack / credential-gated live) is better than most production repos. But endpoint-level behavior tests are nearly absent — both Critical bugs (C1, C2) are one-two-line test cases that don't exist. `conftest.py`'s autouse monkeypatch of `record_request_log` and `count_documents` hides those paths from every test.

### Infra/CI — **CI strong, CD weak**
CI is the most mature asset. The deploy workflow and Terraform are where "defined" and "working" diverge hardest (C3, M10, M11).

---

## 5. What I would do, in order

1. **Fix the five critical/high correctness bugs** (C1, C2, C4, C5, H2) — all are small, test-covered changes.
2. **Wire the reliability layer** (H1) and add boot-time secret validation (H5).
3. **Complete Terraform to a deployable state** (C3): env vars, SQL user, push subscription + IAM, service accounts, monitoring.
4. **Run the real Gemini evaluation** (H4) and record genuine Hit@5/correctness; make the hybrid/rerank decision from evidence.
5. **Re-audit documentation claims** (§3) and correct the record.
6. Then proceed along the roadmap in `docs/roadmap.md`.

---

## 6. Scorecard

| Dimension | Grade | Notes |
|---|---|---|
| Architecture & structure | B+ | Clean layering; route handlers too fat |
| Correctness | C | 5 bugs, 2 user-visible, 1 deployment-blocking |
| Security | B− | Strong fundamentals; unguarded defaults |
| RAG quality | Incomplete | Unmeasured against real embeddings |
| Testing | B− | Excellent strategy, thin execution |
| CI/CD | B− / D | CI: strong. CD/Terraform: non-functional |
| Observability | C | Designed but largely disconnected |
| Documentation | B | Honest about execution gaps; overclaims capabilities |
| Production readiness | **Not ready** | Consistent with the project's own Phase 8.5 gate |
