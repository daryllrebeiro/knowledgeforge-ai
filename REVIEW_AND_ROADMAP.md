# KnowledgeForge AI - Architectural Review & Strategic Roadmap

## 1. Executive Summary & Health Assessment

### Overall System Maturity

| Dimension | Grade | Assessment |
|-----------|-------|------------|
| **Architecture & Structure** | B+ | Clean layered architecture (ingestion/retrieval/generation/security/worker); dependency direction correct. Route handlers are fat (~100-line upload handler with dual strategies inline). Protocol-based generation enables testability. |
| **Code Quality** | B | Type-hinted, Ruff-linted, mypy-strict. Good SQL discipline (parameterized everywhere). Some dead code (reliability.py, decide_dedup, section_aware). |
| **Maintainability** | B- | Modular structure but service layer lives in route handlers. Missing abstraction for sync/async ingestion paths. Batch endpoint calls single-file endpoint directly. |
| **Performance** | C+ | No connection pooling (per-request psycopg.connect). Memory-first upload handling. ivfflat index built on empty table. Embedding cache (migration 011) is a good optimization. |
| **Test Coverage** | B- | Excellent tiering strategy (unit/integration/emulator/live) but thin depth (32 tests). Critical bugs C1/C2 would be caught by simple endpoint tests. Autouse monkeypatch hides telemetry paths. |

### Architectural Philosophy

**Core Strengths:**
- **Multi-tenant isolation at the query level** — tenant_id mandatory in every WHERE clause, proven in CI with real pgvector
- **Sync→async ingestion evolution** — dual paths share core logic via `_ingest_upload`; async path uses GCS + Pub/Sub + worker with 10-minute claim lease
- **Transactional outbox pattern** — ingestion commits document ready state AND extraction trigger atomically; dispatcher publishes with lease-based deduplication
- **Structured extraction pipeline (Phase 2.5)** — separate worker, dead-letter topic, confidence thresholds, review queue, JSONB field filters
- **Fail-closed runtime validation** — `Settings.validate_runtime()` refuses non-development startup with default JWT secret or missing Gemini key
- **Cost-aware design** — embedding cache keyed by content hash + model; local mode for CI/emulator; token usage persisted to request_logs

**Fundamental Structural Risks:**
1. **Service layer absent** — business logic lives in `api.py` route handlers; upload handler is ~100 lines with two full ingestion strategies inline
2. **Reliability layer designed but unwired** — `reliability.py` (CircuitBreaker, with_retry) exists but nothing in production paths calls it
3. **Deployment definition non-functional** — Terraform missing DATABASE_URL, JWT_SECRET_KEY, REDIS_URL, Pub/Sub→worker wiring, IAM, monitoring
4. **Retrieval quality unmeasured** — golden set is 2-question fixture; only numbers from non-semantic hash embeddings
5. **Observability disconnected** — structured logging configured but uvicorn/worker logs unstructured; no end-to-end timeout on `/ask`

### Primary Bottlenecks

1. **Terraform cannot produce a working deployment** — missing runtime env vars, no Pub/Sub push config, no service accounts, no IAM bindings, no monitoring resources. `terraform validate` passes but the graph is disconnected.
2. **Reliability machinery is dead code** — `with_retry`, `CircuitBreaker` defined but never invoked by Gemini, GCS, or Pub/Sub calls. A hung Gemini call pins the request thread indefinitely.
3. **Redis rate limiter broken for horizontal scaling** — uses `monotonic()` (per-process) instead of epoch time for shared state; Lua script computes garbage deltas across replicas. No runtime fallback on Redis outage (every protected endpoint 500s).
4. **Retrieval quality unvalidated** — no real Gemini-keyed evaluation executed; Hit@5 numbers (35-40%) from deterministic SHA-256 hash vectors. Citation bug (C1) inflates measured Hit@5 in eval harness.
5. **Worker idempotency race** — status check admits concurrent deliveries; DELETE+INSERT chunks in separate transactions; no unique constraint on content_hash at insert time.

---

## 2. In-Depth Engineering Review

### Design Patterns & Modularity

**Observations:**
- **Layer separation is clean** — `ingestion/`, `retrieval/`, `generation/`, `security/`, `extraction/`, `worker/` each own a single concern. Dependencies point downward (api → services → db).
- **Protocol-based generation** — `TextGenerator` protocol (`generation/generate.py:13`) makes the ask pipeline testable with `local_answer` (deterministic, no Gemini).
- **Route handlers too fat** — `api.py` is 1,389 lines; `upload_document` (line 710) embeds both sync and async ingestion logic; `batch` endpoint calls single-file handler directly (line 745).
- **Duplicate dedup logic** — `ingestion/dedup.py:decide_dedup` exists but `api.py` reimplements the logic inline (lines 616-618, 681-683). Either use the function or delete it.
- **Section-aware chunking implemented but unreachable** — `ingestion/chunk.py:16` parameter `section_aware` wired through config but no API surface exposes it; `doc_type` filter in `retrieve_chunks` similarly unexposed.

**Leaky Abstractions:**
- `retrieve_chunks` accepts optional `tenant_id` (line 14) but contract invites cross-tenant leak when `None`. Make required or assert non-None outside tests.
- `get_connection()` returns pooled connection when initialized, direct `psycopg.connect` otherwise — keeps scripts working but hides pool absence from type system.
- `_gemini_client()` and `PubSubPublisher` constructed per-request (lines 490, 533) — cached via module globals but not dependency-injected.

### Data Architecture & Persistence

**Schema & Migrations:**
- **14 forward-only migrations** — readable, ordered, idempotent via `scripts/apply_migrations.py`. Tenant-scoped unique hash index (005) correctly fixed earlier global-unique mistake.
- **pgvector with ivfflat** — migration 001 creates index on empty table (`WITH (lists = 100)`). IVF centroids degenerate until `REINDEX` after bulk load. Consider HNSW for filtered multi-tenant access pattern.
- **Extraction schema (014)** — `document_extractions` with JSONB fields (schema-agnostic), partial unique index on `(tenant_id, content_hash, schema_type, schema_version, model)` for idempotency. GIN index on `fields` for JSONB filtering.
- **Extraction jobs + outbox** — `extraction_jobs` with partial unique index `(document_id) WHERE status IN ('queued','processing')` enforces one active job/document. `extraction_outbox` with lease-based claim (`claimed_until`, `FOR UPDATE SKIP LOCKED`).

**Query Patterns:**
- **Tenant scoping mandatory** — every query includes `tenant_id` in WHERE. `retrieve_chunks` builds clauses explicitly (no `(%s IS NULL OR col = %s)` idiom).
- **Hybrid search implemented behind flag** — migration 009 adds `lexical` tsvector column; `retrieve_chunks` combines cosine similarity + `ts_rank` when `hybrid=True`. Local evidence says don't adopt; final call waits on real embeddings.
- **Embedding cache (011)** — `embedding_cache` table keyed by `(model, content_hash)`; `embed_texts_cached` skips provider call on hit.

**Migration Hygiene:**
- Forward-only, no rollback scripts. `scripts/apply_migrations.py` tracks applied migrations in `schema_migrations` table.
- Some migrations add columns used by code that doesn't yet populate them (e.g., 006 telemetry columns never written until R4.5).

### Error Handling & Fault Tolerance

**Resilience Patterns:**
- **CircuitBreaker class exists but unwired** — `reliability.py:29` process-wide breaker with failure threshold + recovery seconds. `api.py` defines `gemini_breaker()` (line 511) but never calls it on generation/embedding paths.
- **with_retry decorator exists but unused** — `reliability.py:11` tenacity wrapper (exponential backoff, 2 attempts). Not applied to `GeminiTextGenerator.generate`, `embed_texts`, GCS, or Pub/Sub.
- **Worker claim lease** — `store.claim_document` (line 296) atomically claims `pending` or expired `processing` (10-min lease). Pub/Sub redelivery cannot double-process while a crashed worker's claim expires.
- **Extraction bounded retry** — one retry with stricter prompt on validation failure (line 155); terminal failures recorded to `failed_extractions` (no silent retry loop). Transient provider errors reset job to `queued` for redelivery; DLQ owns exhaustion.

**Gaps:**
- **No timeouts on external calls** — `GeminiTextGenerator.generate` (line 13 of `generation/gemini.py`), `embed_texts`, GCS upload/download, Pub/Sub publish have no client-side deadlines. A hung call pins the thread.
- **No end-to-end `/ask` timeout** — slow Gemini can hold a worker thread indefinitely.
- **Registration conflates errors** — catches all exceptions, returns 409 (line 371). Unique violation and infrastructure failure indistinguishable.
- **Exception text in API responses** — line 702 returns `f"Unable to ingest document: {exc}"` for all doc types; leaks implementation details.

### Observability & Diagnostics

**Logging:**
- `configure_logging` (line 12) hardcodes INFO, ignores `settings.log_level`; configures only `knowledgeforge.api` logger.
- Uvicorn access logs, worker logs, third-party logs unstructured — "structured JSON logging" claim not met.
- Worker logs nothing: no job start/end/duration/failure (`worker/pipeline.py`, `worker/entrypoint.py`).
- API middleware adds request_id, latency, status, tenant_id to structured log (line 34).

**Metrics & Tracing:**
- **Telemetry columns exist but unused** — migration 006 adds `input_tokens`, `output_tokens`, `cost_estimate` to `request_logs`. Nothing populates them (H3).
- **retrieved_chunk_ids stores document_ids** — column name wrong for content; `api.py:1238` writes document IDs from retrieve result.
- **No distributed tracing** — no OpenTelemetry, no trace context propagation.
- **SLO/monitoring assets buggy** — `infrastructure/monitoring/slo.yaml` filter targets `service_name="knowledgeforge-api"` but Terraform names service `knowledgeforge-api-${var.environment}` (never matches). Dead-letter alert watches live subscription backlog, not dead-letter topic. Nothing in Terraform applies this file.

### Testing & Quality Assurance

**Coverage Gaps:**
- **32 tests total** — thin relative to 15 phases claimed.
- **Unit tests:** `conftest.py` autouse monkeypatch hides `record_request_log` and `count_documents` from every test.
- **Integration tests:** Real Postgres+Redis in CI (good), but only 2 test files (`test_doc_management.py`, `test_conversations_postgres.py`).
- **Security tests:** Phase 4 DoD requires prompt-injection behavior test — `tests/unit/test_prompt.py:28` only asserts hostile string appears in prompt. No model behavior tested. Auth surface test covers 1 route only.
- **C1/C2 bugs would be caught by endpoint tests** — two documents with same page number; delete sync-ingested document.
- **Emulator stack tests:** CI runs full stack (fake-gcs + Pub/Sub emulator + worker + smoke) but smoke test is a single script.
- **Live tier:** Credential-gated (Gemini API key); runs on schedule and main branch.

**CI Pipeline:**
- **Five tiers:** unit → integration → emulator-stack → emulator-chaos → live-external → terraform
- **Quality gates:** ruff, mypy strict, pytest with coverage ratchet (`.coverage-floor`), eval gate (`eval-gate.yml` with ratcheting thresholds)
- **Terraform validate only** — no plan review, no apply in CI (correct).

---

## 3. Critical Modifications & Technical Debt Remediation

| Priority | Category | Component / Module | Issue / Technical Debt | Impact If Ignored | Recommended Fix |
|----------|----------|-------------------|------------------------|-------------------|-----------------|
| **P0** | Correctness | `generation/prompt.py`, `generation/generate.py`, `api.py` | Citation attribution matches on page number across documents (C1) | Users see wrong document citations; eval Hit@5 inflated | Build numbered document list in prompt; per-chunk labels `[doc D, page N]`; parse `(doc, page)` pairs; map citations by chunk identity |
| **P0** | Correctness | `ingestion/store.py`, `api.py` | `DELETE /documents/{id}` returns 404 while deleting (C2) | User sees failure; document actually gone | `RETURNING id, storage_uri`; distinguish row absence (404) from NULL storage_uri (204) |
| **P0** | Reliability | `limits.py` | Redis limiter uses `monotonic()` for shared state (C4) | Rate limiting broken in multi-instance (Cloud Run max_instances=3) | Replace `monotonic()` with `time.time()` in Lua script |
| **P0** | Reliability | `limits.py` | No runtime Redis fallback — 500 on outage (C5) | Every protected endpoint fails when Redis down | Catch connection errors in `check()`; delegate to local limiter; log loudly |
| **P0** | Correctness | `worker/pipeline.py`, `ingestion/store.py` | Worker idempotency race — concurrent deliveries double-process (H2) | Duplicate chunks, wasted embeddings, inconsistent state | Atomic claim: `UPDATE documents SET status='processing' WHERE id=%s AND status='pending'` (rowcount check); DELETE+INSERT chunks in single transaction; add content_hash dedup at insert |
| **P0** | Security | `config.py`, `main.py`, `worker/entrypoint.py` | JWT secret default with no boot validation (H5) | Tokens forgeable if env var forgotten in deployment | Startup check: refuse non-development traffic when secret is default or <32 chars |
| **P1** | Reliability | `reliability.py`, `generation/gemini.py`, `ingestion/embed.py`, `worker/cloud.py` | Retry/timeout/circuit-breaker code dead (H1) | Hung external calls pin threads; no graceful degradation | Wire `with_retry` on Gemini generate/embed, GCS, PubSub; wire `CircuitBreaker` on all Gemini calls; add client timeouts (30s default) |
| **P1** | Data | `migrations/001_initial.sql` | ivfflat index built on empty table (H6) | Degenerate centroids → poor recall/latency until reindex | Build index after first bulk load; document `REINDEX` step; evaluate HNSW |
| **P1** | Observability | `ingestion/store.py`, `api.py`, `retrieval/retrieve.py` | Token/cost telemetry never written (H3) | `/admin/usage` cost always 0; no cost visibility | Capture Gemini usage metadata in embed/generate; write to request_logs; fix `retrieved_chunk_ids` to store chunk IDs |
| **P1** | Infra | `infrastructure/terraform/main.tf` | Terraform cannot deploy working system (C3) | No production deployment possible | Add DATABASE_URL, JWT_SECRET_KEY, REDIS_URL to API/worker; Pub/Sub push_config + OIDC; service accounts + IAM; Cloud SQL user; monitoring resources |
| **P1** | Testing | `tests/` | Critical bugs C1/C2 lack regression tests | Regressions undetected | Add endpoint tests: two docs with page 4 → correct citation mapping; delete sync document → 204 |
| **P2** | Code Quality | `ingestion/dedup.py`, `api.py` | `decide_dedup` unused; logic duplicated | Maintenance burden, inconsistency | Use `decide_dedup` in `_ingest_upload` or delete function |
| **P2** | Code Quality | `retrieval/retrieve.py` | `tenant_id` optional but contract invites leak | Future cross-tenant bug | Make `tenant_id` required; assert non-None outside tests |
| **P2** | DX | `api.py` | Route handlers too fat; service layer missing | Hard to test, extend, reason about | Extract application service layer; thin routes; shared sync/async ingestion core |

### Before/After Architecture Patterns for Top P0/P1 Concerns

#### C1: Citation Attribution Fix

**Before (Broken):**
```python
# generation/prompt.py
for i, chunk in enumerate(chunks):
    prompt += f"[page {chunk.page}] {chunk.text}\n"

# generation/generate.py
CITATION_PATTERN = re.compile(r"\[page (\d+)\]")

# api.py
citations = [
    CitationResponse(document_id=document_id, page=citation.page)
    for citation in answer.citations
    for document_id, chunk in retrieved
    if chunk.page == citation.page  # WRONG: matches ALL docs with same page
]
```

**After (Fixed):**
```python
# generation/prompt.py
for doc_idx, (document_id, chunk) in enumerate(retrieved, 1):
    prompt += f"[doc {doc_idx}, page {chunk.page}] {chunk.text}\n"

# generation/generate.py
CITATION_PATTERN = re.compile(r"\[doc (\d+), page (\d+)\]")
# parse returns (document_index, page) pairs

# api.py
doc_by_number = {num: doc_id for doc_id, num in document_numbers.items()}
citations = [
    CitationResponse(document_id=doc_by_number[citation.document_index], page=citation.page)
    for citation in answer.citations
    if citation.document_index in doc_by_number
]
```

#### H2: Worker Idempotency Fix

**Before (Race):**
```python
# ingestion/jobs.py
def should_process(status: str) -> bool:
    return status in {"pending", "failed"}  # admits concurrent deliveries

# worker/pipeline.py
with connection.transaction():
    cursor.execute("DELETE FROM chunks WHERE document_id = %s", (job.document_id,))
store_chunks(connection, job.document_id, chunks, embeddings)  # SEPARATE transaction
```

**After (Atomic Claim + Single Transaction):**
```python
# ingestion/store.py
def claim_document(connection, document_id, tenant_id) -> bool:
    """Atomically claim; admits pending OR expired processing (10-min lease)."""
    cursor.execute("""
        UPDATE documents
        SET status = 'processing', status_changed_at = now()
        WHERE id = %s AND tenant_id = %s
          AND (status = 'pending'
               OR (status = 'processing' AND status_changed_at < now() - interval '10 minutes'))
    """, (document_id, tenant_id))
    return cursor.rowcount == 1

# worker/pipeline.py
def process_ingestion_job(job, settings):
    # Claim is atomic — only one worker wins
    with psycopg.connect(settings.database_url) as connection:
        if not claim_document(connection, job.document_id, job.tenant_id):
            return  # duplicate delivery, acknowledged
        
        # ... extract, chunk, embed ...
        
        # Single transaction: delete old chunks + insert new + mark ready + create extraction job
        with connection.transaction():
            cursor.execute("DELETE FROM chunks WHERE document_id = %s", (job.document_id,))
            store_chunks(connection, job.document_id, chunks, embeddings)
            insert_extraction_job(connection, ...)  # transactional outbox
```

---

## 4. Optimization & Enhancement Recommendations

### Performance & Scalability

| Area | Recommendation | Effort | Impact |
|------|----------------|--------|--------|
| **Connection Pooling** | Adopt `psycopg_pool.ConnectionPool` (already implemented in `db.py:21` — wire it everywhere) | Low | Eliminates TCP+auth handshake per request; prevents Cloud SQL connection exhaustion |
| **Embedding Cache** | Already implemented (migration 011, `embed_texts_cached`) — keyed by `(model, content_hash)` | Done | Repeated uploads of identical content skip embedding calls entirely |
| **Async Processing** | Ingestion worker + extraction worker + outbox dispatcher already separated; add autoscaling on Pub/Sub backlog | Medium | Horizontal scaling; blast radius isolation |
| **Index Strategy** | After corpus load: `REINDEX` ivfflat or migrate to HNSW; measure P95 at 1k/10k/100k chunks; partial indexes for large tenants | Medium | Retrieval latency & recall at scale |
| **Hybrid Search Decision** | Execute R3.3 Gemini-keyed eval; if exact-match questions miss, adopt `tsvector` hybrid (already behind flag); else record decision | Medium | Better precision for IDs, error codes |
| **Streaming Upload Guard** | Already implemented (R4.2) — early `Content-Length` check + chunked read with hard cutoff | Done | Memory safety for large uploads |
| **Rate Limiter Eviction** | In-memory limiter `MAX_BUCKETS=10000` with idle eviction (implemented R4.8) — verify under flood | Done | Bounded memory under distinct-subject flood |

### Developer Experience (DX) & Tooling

| Area | Recommendation | Effort | Impact |
|------|----------------|--------|--------|
| **Local Dev Environment** | `docker compose up -d` spins full stack (Postgres, Redis, fake-gcs, Pub/Sub emulator, migrations, API, workers, smoke test) | Done | Zero-config local development |
| **Linting/Format/Type** | Ruff (E,F,I,B,UP), mypy strict, pre-commit — all in CI | Done | Consistent style, catch bugs early |
| **Coverage Ratchet** | `.coverage-floor` + `scripts/coverage_ratchet.py` — no PR may decrease coverage | Done | Prevents test rot |
| **Eval Gate** | `eval-gate.yml` runs golden-set eval on release branches; floors ratchet up only | Done | Prevents retrieval regression |
| **Migrations** | Forward-only SQL + `scripts/apply_migrations.py` — simple, auditable | Done | No Alembic complexity |
| **Container Hygiene** | Non-root USER, HEALTHCHECK, `uv sync --frozen`, pin `fake-gcs-server` digest | Medium | Reproducible builds; security posture |
| **Deploy Pipeline** | `terraform-plan.yml` posts plan for review; apply manual-approved; document GitHub env protection | Medium | Safe CD with human gate |

### Security & Hardening Quick-Wins

| Area | Recommendation | Effort | Impact |
|------|----------------|--------|--------|
| **Boot-time Secret Validation** | `Settings.validate_runtime()` — refuse non-dev startup with default JWT secret or missing Gemini key (already implemented R2.4) | Done | Fail-closed on misconfiguration |
| **Input Validation** | Pydantic models on all endpoints; upload size caps; document quotas; file type allowlist | Done | Defense in depth |
| **Auth Rate Limiting** | Keyed by caller IP (`_client_subject`), separate bucket for auth endpoints | Done | Brute-force mitigation |
| **Tenant Isolation** | Query-level scoping mandatory; no unfiltered branch in `retrieve_chunks` | Done | Cross-tenant leak prevention |
| **Security Headers** | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, Permissions-Policy, Cache-Control on auth | Done | Browser-side hardening |
| **API Keys** | `kf_`-prefixed, SHA-256 hashed at rest, scoped, revocable, `X-API-Key` auth | Done | Programmatic access without JWT |
| **OIDC Verification** | Worker push entrypoint verifies Pub/Sub OIDC token against `WORKER_OIDC_AUDIENCE` (configurable) | Medium | Defense in depth beyond Cloud Run IAM |
| **Error Message Sanitization** | Replace raw exception text in responses with generic messages; log detail server-side | Low | Information disclosure prevention |

---

## 5. Future Engineering & Feature Roadmap

### Phase 1: Stabilization & Hardening (Short-Term: Weeks 1–4)

**Goal:** Fix all P0/P1 issues; make Terraform deployable; wire reliability layer; execute real evaluation.

| Item | Description | Dependencies |
|------|-------------|--------------|
| **Fix C1: Citation Attribution** | Prompt numbering, citation parsing, mapping by chunk identity | None |
| **Fix C2: Delete 404 Bug** | `RETURNING id, storage_uri`; distinguish not-found vs no-storage | None |
| **Fix C4: Redis Limiter Clock** | `monotonic()` → `time.time()` in Lua script | None |
| **Fix C5: Redis Fallback** | Catch connection errors; degrade to local limiter with warning log | None |
| **Fix H2: Worker Idempotency** | Atomic claim lease; single transaction for chunk delete+insert+outbox | Migration 008 |
| **Fix H5: JWT Boot Validation** | Startup check for default/short secret in non-development | None |
| **Wire Reliability Layer (H1)** | Apply `with_retry` + timeouts + `CircuitBreaker` to all external calls (Gemini, GCS, Pub/Sub) | None |
| **Fix H3: Token Telemetry** | Capture usage metadata in embed/generate; write to request_logs; fix chunk IDs | None |
| **Complete Terraform (C3)** | API/worker env vars, SQL user, push subscription + OIDC, service accounts, IAM, monitoring | R5.1–R5.7 |
| **Run Real Gemini Evaluation (H4)** | Execute `run_eval.py` + `run_phase12_eval.py` with real embeddings; record Hit@5/correctness | Credentials |
| **Re-audit Documentation Claims** | Correct all overclaims in `Plan.md`, `pending-completion-and-gaps.md`, `validation-status.md` | None |

**Definition of Done:** All P0/P1 items test-covered; Terraform plan produces connected graph; real eval numbers in `docs/decisions.md`; documentation claims match code.

### Phase 2: Architectural Scaling & Performance (Medium-Term: Month 2–3)

**Goal:** Service layer extraction; retrieval optimization; horizontal scaling prep; workflow automation.

| Item | Description | Dependencies |
|------|-------------|--------------|
| **Extract Service Layer** | Move ingestion/ask logic from `api.py` routes to `services/`; thin routes; shared sync/async core | Phase 1 complete |
| **Retrieval Optimization** | Based on R3 evidence: adopt hybrid/rerank or record decision; tune ivfflat→HNSW; add partial indexes | Phase 1 R3 complete |
| **Worker Autoscaling** | Cloud Run autoscaling on Pub/Sub backlog; flow-control matching Gemini quotas | Terraform complete |
| **Read Replica** | Cloud SQL read replica for retrieval if write contention measured | Load test evidence |
| **Multi-region Strategy** | Document data-residency decision; only implement when real requirement exists | Product decision |
| **Backup/Restore Automation** | Scheduled PITR verification; documented restore drill | Phase 1 launch |
| **Cost Optimization** | Per-tenant budget alerts (email/webhook); embedding cache hit-rate monitoring | Telemetry complete |

**Definition of Done:** Service layer extracted with tests; retrieval config decided from evidence; autoscaling policies in Terraform; cost dashboards operational.

### Phase 3: Next-Generation Feature Expansion (Long-Term: Month 4–6+)

**Goal:** High-value user-facing features, advanced integrations, platform capabilities.

| Feature Name | Business/Technical Value | Complexity | Architectural Prerequisites |
|--------------|--------------------------|------------|----------------------------|
| **F1: Conversation Experience** | Chat endpoints with persisted history, SSE streaming, follow-up rewrite | Medium | Service layer extracted; streaming generation refactored |
| **F2: Retrieval Upgrades** | Hybrid search/reranking (evidence-driven); metadata filters (date, filename); per-document scope | Low–Medium | Real eval complete; hybrid decision made |
| **F3: Document Management** | List/detail with versions/supersession; re-ingestion; chunk preview; additional formats (.pptx, CSV, OCR) | Medium | Phase 2 retrieval; worker blast-radius isolation |
| **F4: Multi-user Tenants** | Roles (owner/member), invitations; unique email per tenant (migration + product decision) | High | Auth service extraction; email sender integration |
| **F5: Admin Console** | Tenant list, usage, dead-letter inspection, failed-ingestion triage; role-gated | Medium | Admin role model; usage telemetry complete |
| **F6: Continuous QA** | LLM-as-judge correctness scoring; GCS 500 chaos injection; coverage/eval ratchets | Medium | Eval harness mature; chaos stack extended |
| **F7: Scale-out Readiness** | HNSW migration; partial indexes; read replicas; multi-region (if required) | High | Phase 2 measurements; corpus at scale |
| **F8: Trust & Compliance** | Privacy/ToS legal review; DPA template; status page; SOC2 controls (if targeting enterprise) | High | Legal counsel; incident communication plan |

---

## 6. Technical Decision Log (ADR Recommendations)

The engineering team must formally decide on the following before scaling further:

### ADR-001: Async Queue Worker Model
**Context:** Current workers are pull-based (`pull_entrypoint.py`) for ingestion and extraction; outbox dispatcher is a scheduled Cloud Run Job. Push-based workers with Pub/Sub push + OIDC verification are partially implemented (`worker/entrypoint.py`).
**Decision Required:** Standardize on pull vs. push for all workers. Push reduces latency but requires OIDC verification + Cloud Run invoker IAM. Pull is simpler, naturally backpressure-friendly, but adds polling overhead.
**Recommendation:** Pull for ingestion/extraction workers (current); push for outbox dispatcher (current). Document in `docs/decisions.md` with trade-offs.

### ADR-002: Event-Driven Architecture Adoption
**Context:** Transactional outbox pattern implemented for extraction trigger. No domain events for document lifecycle (created, ready, failed, deleted, superseded).
**Decision Required:** Adopt domain events for cross-service communication (e.g., document deletion → GCS cleanup, search index update, conversation cleanup). Define event schema, delivery guarantees, and consumer contracts.
**Recommendation:** Extend outbox pattern to document lifecycle events; use same dispatcher infrastructure; start with `document.deleted` for GCS cleanup.

### ADR-003: Database Sharding / Partitioning Strategy
**Context:** Single PostgreSQL instance with tenant-scoped queries. `chunks` table grows with every document. IVF index on embeddings. No partitioning.
**Decision Required:** Define sharding/partitioning strategy before 100k+ chunks per tenant. Options: (a) tenant-level schema separation, (b) `chunks` partitioning by `tenant_id` + `document_id`, (c) read replicas for retrieval, (d) separate vector DB (Pinecone, Weaviate).
**Recommendation:** Start with (b) native partitioning + read replica; evaluate vector DB only if pgvector latency/recall fails at scale. Document in `docs/decisions.md` with capacity thresholds.

### ADR-004: Multi-Model Generation Strategy
**Context:** Hardcoded to Gemini (`gemini-2.0-flash` for generation, `gemini-embedding-001` for embeddings). `local_generation`/`local_embeddings` flags for emulator. No abstraction for model routing.
**Decision Required:** Support multiple generation/embedding models (e.g., Gemini Flash/Pro, local models via Ollama, OpenAI-compatible). Define model registry, capability routing (cost/latency/quality), and fallback chains.
**Recommendation:** Add `ModelRouter` protocol; config-driven model selection per task (generation vs embedding vs extraction); keep Gemini as default. Implement when multi-model requirement is real, not speculative.

---

*Document generated from exhaustive architectural review of KnowledgeForge AI codebase (commit HEAD). All findings traceable to source locations and existing documentation (`docs/architecture-review.md`, `docs/roadmap.md`, `docs/validation-status.md`).*