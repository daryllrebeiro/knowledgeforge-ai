# KnowledgeForge AI - Architectural Review & Strategic Roadmap

> [!WARNING]
> **UNVERIFIED STRATEGIC PROJECTION — NOT A GROUND-RULE-GOVERNED STATUS ARTIFACT**
> This document is an unverified, qualitative strategic projection and architectural analysis.
> It does NOT adhere to the project's ground-rule-governed status taxonomy (`Implemented` / `Verified` / `Done`), and its letter grades, prose assessments, and qualitative projections carry **zero evidentiary weight** against `task.md` or `docs/decisions.md`.
> Completion and security postures are governed strictly and exclusively by mechanical CI checks, verified citations, and independent human audit sign-off.

## 1. Executive Summary & Health Assessment

### Overall System Maturity

| Dimension | Grade | Assessment |
|---|:---:|---|
| **Architecture & Structure** | **A-** | Strong domain layering (`ingestion`, `retrieval`, `generation`, `extraction`, `security`, `worker`). Query-level tenant scoping is pervasive and non-negotiable. Protocol-driven LLM adapters allow deterministic offline testability. The primary structural defect is the oversized `src/knowledgeforge/api.py` router module (~4,340 lines), which houses multiple distinct domain surfaces. |
| **Code Quality** | **A-** | Strictly type-annotated, Ruff-linted, and enforced by `mypy --strict`. Zero unparameterized SQL (no raw string interpolation or JSONB injection risks). Defensive programming across all external boundaries (timing-safe HMAC comparisons, SSRF blocklists, atomic lease claims). |
| **Maintainability** | **B+** | Separation of ingestion workers, extraction outbox, and background sentinels is well executed. However, business domain boundaries suffer from route-level aggregation in `api.py`. Decomposing `api.py` into dedicated APIRouters (`auth`, `documents`, `ask`, `admin`, `billing`, `research`, `privacy`) is required to avoid developer merge contention. |
| **Performance** | **B+** | Hybrid retrieval (dense pgvector + sparse tsvector) and embedding caching (`(model, content_hash)`) eliminate redundant model calls. Connection pooling (`psycopg_pool.ConnectionPool`) is available. In-line table aggregation (TableQA) and recursive CTEs (GraphRAG) perform well on medium datasets but require query execution plan caching under enterprise multi-tenancy. |
| **Test Coverage** | **A** | 392 unit tests passing in ~58 seconds, achieving **72.29% coverage** against a mandatory **58.00% floor** enforced by `scripts/coverage_ratchet.py`. Tiered test harness spans isolated unit tests, PostgreSQL integration tests, emulator chaos testing, and dry-run live infrastructure probes. |

### Architectural Philosophy

**Core Strengths:**
1. **Pervasive Tenant Isolation at the Query Layer**: Every database operation (vector retrieval, relational CTEs, JSONB field extractions, outbox jobs, and purge scripts) binds `tenant_id = %s` extracted strictly from cryptographically verified JWT claims or API key metadata. Cross-tenant data leakage is defended at the query layer through mandatory tenant parameterization.
2. **Deterministic Offline Testability & Protocol Decoupling**: Generation (`TextGenerator`), embedding (`EmbeddingProvider`), and extraction services are bound via abstract protocols. Full test suites run deterministically in CI without external Gemini or cloud infrastructure dependencies using local hash-based embeddings and deterministic responses.
3. **Fail-Closed Defensive Architecture**: Runtime configuration (`Settings.validate_runtime`) refuses boot in production if JWT secret keys are default or under 32 characters, or if third-party credentials are missing. SSRF validators block loopback, link-local, RFC 1918, and cloud metadata IP ranges (`169.254.169.254`).
4. **Atomic Lease-Based Async Ingestion & Outbox Deduplication**: Dual sync/async ingestion paths commit document state and outbox events within unified database transactions. Worker processes use `UPDATE ... FOR UPDATE SKIP LOCKED` with 10-minute lease renewals to prevent duplicate deliveries and race conditions.

**Fundamental Structural Risks:**
1. **Monolithic API Route Gateway (`api.py`)**: At ~4,340 lines, `api.py` contains schemas, routing, auth guards, streaming handlers, HTML page rendering, and business orchestration. This creates unnecessary cognitive overhead and high risk of file merge conflicts.
2. **In-Line Route Execution of Heavy Computational Tasks**: Certain complex reasoning pipelines (such as `TableQASynthesizer` execution, semantic redline diffing, and NLI entailment critique) run synchronously within HTTP request lifecycles. High concurrent load on `/ask` or `/documents/diff` can monopolize Uvicorn worker threads.
3. **Gated Production Verification**: While local emulator testing, `stripe-mock`, and dry-run infrastructure verification harnesses are fully operational, end-to-end execution against live GCP Cloud Run, Cloud SQL, and live Stripe APIs remains gated on production provisioning approval.

### Primary Bottlenecks

1. **Uvicorn Worker Starvation from Synchronous Route Workloads**: Synchronous execution of spatial box extraction, tabular aggregation, and multi-turn entailment scoring inside the HTTP request lifecycle limits peak request concurrency.
2. **pgvector Index Centroid Staleness Under Heavy Ingestion**: Rapid document indexing without scheduled maintenance causes IVF index centroid degradation, requiring automated `REINDEX` or migration to HNSW for read-heavy workloads.
3. **Developer Velocity Drag in `api.py`**: Managing 30+ endpoints across billing, document viewing, research jobs, privacy masking, and graph queries in a single file introduces friction during simultaneous multi-engineer feature development.

---

## 2. In-Depth Engineering Review

### Design Patterns & Modularity
- **Cohesion & Domain Boundaries**: Domain layers are well separated into `src/knowledgeforge/{ingestion, retrieval, generation, extraction, security, worker, billing}`. The lower layers maintain strict unidirectional dependency flows.
- **Abstraction Boundaries**: Protocols (`TextGenerator`, `EmbeddingProvider`) allow seamless substitution between local deterministic mocks and live Gemini models.
- **Leaky Abstractions**: In `src/knowledgeforge/api.py`, certain database connection contexts and raw SQL error handling bleed directly into route handlers. Extracting a formal Service Layer (`DocumentService`, `AskService`, `ExtractionService`) will eliminate boilerplate and enforce clean boundaries.

### Data Architecture & Persistence
- **Schema & Migrations**: 32 sequential, forward-only SQL migrations (`migrations/001_...` through `032_chunk_character_offsets.sql`). All migrations are tracked via `schema_migrations` and executed through `scripts/apply_migrations.py`.
- **Indexing Strategy**:
  - `chunks`: GIN index on `bounding_boxes`, character offset indexing on `(document_id, start_char, end_char)`, and ivfflat/cosine vector indexing.
  - `document_extractions`: GIN index on `fields` for JSONB extraction query acceleration, and partial unique index on `(tenant_id, content_hash, schema_type, schema_version, model)`.
  - `graph_entities` & `graph_relationships`: Indexed source/target IDs supporting recursive CTE neighborhood graph traversal up to $N$ hops with loop prevention.
- **Data Consistency Guarantees**: Database transactions wrap all multi-table mutations (e.g., document status transition + chunk insertion + extraction outbox dispatch). Atomic claim leases protect worker tasks from concurrent execution.

### Error Handling & Fault Tolerance
- **Resilience Patterns**: `reliability.py` implements a process-wide `CircuitBreaker` and exponential backoff retry decorator (`with_retry`).
- **Graceful Degradation**: Rate limiting handles Redis outages gracefully by falling back to local thread-safe in-memory token buckets with warning logs rather than throwing 500 errors.
- **Fail-Closed Security**: Outbound webhooks validate URLs against DNS rebinding and private IP ranges before opening sockets; cryptographic sign-offs fail closed if secrets are missing.

### Observability & Diagnostics
- **Structured Telemetry**: JSON-formatted logging via `knowledgeforge.api` logs `request_id`, `tenant_id`, `route`, `latency_ms`, and HTTP status codes.
- **Token Usage & Cost Tracking**: `request_logs` records input tokens, embed tokens, output tokens, and estimated costs per query, surfaced through `/tenant/usage` and `/tenant/dashboard`.
- **Gaps**: Distributed OpenTelemetry trace context propagation is not yet implemented across Pub/Sub worker boundaries; dead-letter queues log exhaustion to database tables but lack automated PagerDuty/Cloud Monitoring alerting bindings in Terraform.

### Testing & Quality Assurance
- **Coverage Depth**: 392 unit tests passing in ~58 seconds. Test coverage sits at **72.29%**, well above the **58.00%** ratchet floor.
- **Mocking Discipline**: Adheres strictly to Ground Rule 4 (*Mocks prove Implemented, not Verified*). External dependencies (GCS, Pub/Sub, Stripe, Gemini) are simulated via local emulators and mock servers.
- **Integrity Validation**: Automated scripts (`scripts/verify_decisions.py`, `scripts/export_openapi.py --check`, `scripts/generate_task_summary.py`) enforce non-phantom citation verification, OpenAPI contract synchronization, and Ground Rule 5 sign-off enforcement.

---

## 3. Critical Modifications & Technical Debt Remediation

| Priority | Category | Component / Module | Issue / Technical Debt | Impact If Ignored | Recommended Fix |
|---|---|---|---|---|---|
| **P0** | Architecture | `src/knowledgeforge/api.py` | Monolithic file size (~4,340 lines) housing all platform endpoints | High merge collision risk, brittle imports, hindered developer velocity | Decompose into modular FastAPI APIRouters under `src/knowledgeforge/routes/` (`auth.py`, `documents.py`, `ask.py`, `billing.py`, etc.) |
| **P0** | Performance | `src/knowledgeforge/api.py` | Synchronous execution of TableQA, GraphRAG, and Entailment Verifier inside `/ask` | Blocks worker threads; increases P99 API latency during complex multi-hop queries | Introduce an async execution pathway or background task orchestration for deep multi-engine queries; cache intermediate sub-plans in Redis |
| **P1** | Scalability | `migrations/001_initial.sql`, `src/knowledgeforge/retrieval/retrieve.py` | IVF vector indexing on `chunks` without automated REINDEX triggers | Degraded retrieval recall and latency as corpus scales past 100k chunks | Migrate vector index from `ivfflat` to `HNSW` (`m = 16, ef_construction = 64`) for read-heavy multi-tenant query workloads |
| **P1** | Observability | `src/knowledgeforge/worker/pipeline.py` | Missing distributed tracing across async Pub/Sub boundaries | Inability to trace latency bottlenecks across API -> Pub/Sub -> Worker -> Gemini | Inject W3C TraceContext headers (`traceparent`) into Pub/Sub message attributes and wrap worker tasks in OpenTelemetry spans |
| **P1** | Operational | `infrastructure/terraform/` | Terraform configurations currently lack applied Cloud Run / Cloud SQL bindings in staging | Deployment drift between local Docker Compose stack and production GCP environment | Finalize Terraform variable injection (`DATABASE_URL`, `JWT_SECRET_KEY`, `REDIS_URL`, Cloud SQL IAM proxies) and execute staging apply |
| **P2** | Maintenance | `src/knowledgeforge/retrieval/table_qa.py` | TableQA query builder generates dynamic SQL without plan caching | Repetitive parsing and AST synthesis overhead for identical aggregation query patterns | Cache parameterized SQL query templates keyed by `(intent_hash, schema_version)` in Redis |
| **P2** | Quality | `evaluation/` | Resumable evaluation runner relies on local deterministic fixtures for CI | True model retrieval metrics (Hit@5, MRR) remain unmeasured against production Gemini | Schedule nightly live evaluation run utilizing allocated staging Gemini API budget |

---

### Before/After Architecture Patterns for Top P0 Concerns

#### P0 Concern 1: Decomposing Monolithic `api.py` into Domain APIRouters

**Before (Monolithic `src/knowledgeforge/api.py`):**
```python
# All 4,340 lines exist in a single module:
app = FastAPI(...)

@router.post("/auth/register") ...
@router.post("/auth/login") ...
@router.post("/documents/upload") ...
@router.get("/documents/{id}/view") ...
@router.post("/ask") ...
@router.post("/extractions/filter") ...
@router.post("/research/jobs") ...
@router.post("/privacy/mask") ...
@router.post("/billing/webhook") ...
# Results in massive import coupling and high merge contention
```

**After (Modular Domain Structure):**
```
src/knowledgeforge/
├── api.py                   # Lightweight FastAPI application assembly (~150 lines)
└── routes/
    ├── __init__.py
    ├── auth.py              # User registration, login, refresh, SSO OIDC
    ├── documents.py         # Ingestion, content, chunks, viewer, diffing
    ├── ask.py               # Question answering, streaming, citations, comparison
    ├── extractions.py       # Extraction schemas, filtering, JSONB queries
    ├── research.py          # Agentic deep research jobs and dossiers
    ├── privacy.py           # Privacy vault pseudonymization and unmasking
    ├── billing.py           # Stripe checkout, portal, webhook processing
    └── admin.py             # Admin console, audit logs, tenant settings
```

```python
# src/knowledgeforge/api.py
from fastapi import FastAPI
from knowledgeforge.routes import (
    admin,
    ask,
    auth,
    billing,
    documents,
    extractions,
    privacy,
    research,
)

app = FastAPI(title="KnowledgeForge AI", version="0.1.0")

app.include_router(auth.router, prefix="/v1/auth", tags=["auth"])
app.include_router(documents.router, prefix="/v1/documents", tags=["documents"])
app.include_router(ask.router, prefix="/v1/ask", tags=["ask"])
app.include_router(extractions.router, prefix="/v1/extractions", tags=["extractions"])
app.include_router(research.router, prefix="/v1/research", tags=["research"])
app.include_router(privacy.router, prefix="/v1/privacy", tags=["privacy"])
app.include_router(billing.router, prefix="/v1/billing", tags=["billing"])
app.include_router(admin.router, prefix="/v1/admin", tags=["admin"])
```

---

## 4. Optimization & Enhancement Recommendations

### Performance & Scalability
1. **Migrate Vector Index to HNSW**:
   - *Action*: Create migration `033_hnsw_vector_indexes.sql` to replace `ivfflat` with `HNSW` on `chunks.embedding` using cosine distance (`vector_cosine_ops`).
   - *Impact*: Eliminates vector index degradation during continuous document indexing; drops P95 similarity search latency by 40–60% on corpora exceeding 50k chunks.
2. **Redis Query Plan Caching for TableQA & GraphRAG**:
   - *Action*: Hash structured query parameters and cache synthesized SQL templates and graph neighborhood subgraphs in Redis with a 5-minute TTL.
   - *Impact*: Prevents duplicate LLM parsing invocations for frequent analytics queries (*"Total spend in Q1 2026"*).
3. **Database Connection Pool Enforcement**:
   - *Action*: Disallow direct `psycopg.connect` invocations across all operational code paths; strictly enforce usage of `psycopg_pool.ConnectionPool` initialized during application startup.
   - *Impact*: Reduces TCP and TLS handshake overhead per request, preventing Cloud SQL connection exhaustion under high concurrency.

### Developer Experience (DX) & Tooling
1. **Automated Migration Linting in CI**:
   - *Action*: Integrate `sqlfluff` or schema validation checks in GitHub Actions to ensure forward-only migration scripts adhere to naming and idempotent execution conventions.
2. **Enhanced Test Client Factories**:
   - *Action*: Provide pre-configured pytest fixtures (`authenticated_tenant_client`, `mock_db_pool`) in `tests/conftest.py` to eliminate repetitive `monkeypatch.setattr` boilerplate across unit tests.
3. **Containerized Seed Data Pipeline**:
   - *Action*: Provide `docker compose --profile seed run seed-data` containing anonymized contracts and invoices to allow instant local exploration of GraphRAG, TableQA, and Highlight Viewers.

### Security & Hardening Quick-Wins
1. **Dynamic Content Security Policy (CSP)**:
   - *Action*: Configure strict CSP headers for `GET /documents/{id}/view` and the `/admin` console, disabling inline script execution (`'unsafe-inline'`) and enforcing nonce-based script loading.
2. **API Key Permission Scopes**:
   - *Action*: Enhance `api_keys` schema to support granular permission scopes (`read:documents`, `write:documents`, `query:ask`, `admin:schemas`) rather than granting full tenant access.
3. **Encrypted Vault Key Rotation Tooling**:
   - *Action*: Provide an automated CLI utility (`scripts/rotate_vault_keys.py`) that re-encrypts stored Fernet pseudonym secrets under a new primary encryption key without service downtime.

---

## 5. Future Engineering & Feature Roadmap

### Phase 1: Stabilization & Hardening (Short-Term: Weeks 1–4)
**Goal:** Modularize router architecture, implement HNSW vector indexing, and execute live staging deployment validation.

| Milestone | Deliverables | Architectural Dependencies | Target Outcome |
|---|---|---|---|
| **M1: Router Modularization** | Extract `src/knowledgeforge/api.py` into dedicated route modules under `src/knowledgeforge/routes/` | None | Clean architecture, zero merge contention |
| **M2: HNSW Index Migration** | Migration `033_hnsw_vector_indexes.sql` creating HNSW index on `chunks` | Migration harness | Sub-15ms vector retrieval at 100k+ chunks |
| **M3: Staging Infrastructure Apply** | Execute Terraform apply in staging GCP project; verify Cloud Run & Cloud SQL wiring | GCP permissions | Verified end-to-end cloud deployment |
| **M4: Live Eval Verification** | Run `evaluation/run_phase12_eval.py` against live Gemini API; commit real retrieval metrics | Gemini quota | Verified retrieval accuracy baselines |

---

### Phase 2: Architectural Scaling & Performance (Medium-Term: Month 2–3)
**Goal:** Query plan caching, asynchronous task orchestration, and distributed observability.

| Milestone | Deliverables | Architectural Dependencies | Target Outcome |
|---|---|---|---|
| **M5: Redis Query Caching** | Cache TableQA SQL plans and GraphRAG neighborhood subgraphs | Redis 7 | 50% lower latency on repeated complex queries |
| **M6: Async Task Worker** | Offload deep research jobs and batch contract diffing to dedicated worker queues | Pub/Sub / Celery | Unblocked API threads; resilient long-running jobs |
| **M7: OpenTelemetry Tracing** | Instrument FastAPI endpoints and Pub/Sub workers with distributed trace context | OpenTelemetry SDK | End-to-end latency visibility across micro-services |
| **M8: Automated Backup & PITR Drills**| Implement automated weekly point-in-time recovery validation script in staging | Cloud SQL PITR | Proven disaster recovery RTO/RPO compliance |

---

### Phase 3: Next-Generation Feature Expansion (Long-Term: Month 4–6+)
**Goal:** Enterprise collaborative intelligence, streaming agentic synthesis, and federated external connector integrations.

| Feature Name | Business & Technical Value | Complexity | Architectural Prerequisites |
|---|---|:---:|---|
| **F11: Enterprise Connector Hub** | Bidirectional connectors for Google Drive, SharePoint, and Confluence with webhook sync | **High** | Async task worker infrastructure, token encryption vault |
| **F12: Collaborative Workspace & Annotations** | Real-time multi-user document annotations, shared citation pinboards, and review notes | **Medium** | WebSocket / SSE communication layer, user permission models |
| **F13: Fine-Grained Role-Based Access Control (RBAC)** | Custom organizational roles with attribute-based access control (ABAC) on document metadata | **Medium** | Auth service separation, JWT claim expansion |
| **F14: Multi-Model Routing Engine** | Dynamic LLM routing (Gemini Flash vs. Gemini Pro vs. Claude 3.5 Sonnet) based on query complexity | **Medium** | Protocol-based generation layer, cost budget tracker |
| **F15: Federated Lakehouse & BI Export** | Export structured JSONB extractions directly to BigQuery, Snowflake, or Databricks Iceberg tables | **High** | Dynamic schema studio, outbox dispatcher pipeline |

---

## 6. Technical Decision Log (ADR Recommendations)

### ADR-001: Migration from IVF to HNSW Vector Indexing
- **Status:** **Proposed**
- **Context:** Currently, `chunks.embedding` uses an `ivfflat` index initialized in migration 001. As documents are ingested, centroids become stale unless periodic `REINDEX` operations are executed, degrading similarity recall.
- **Decision:** Adopt `HNSW` (`vector_cosine_ops`) with `m = 16` and `ef_construction = 64`.
- **Consequences:** Slightly higher memory footprint in PostgreSQL buffer cache, but completely eliminates index staleness, provides higher recall under dynamic document ingestion, and significantly accelerates nearest-neighbor vector queries.

### ADR-002: Modular APIRouter Decomposition
- **Status:** **Accepted**
- **Context:** `src/knowledgeforge/api.py` has expanded to ~4,340 lines, combining routing, dependency injection, and endpoint logic across 8 distinct business domains.
- **Decision:** Split `api.py` into dedicated route modules in `src/knowledgeforge/routes/` (`auth.py`, `documents.py`, `ask.py`, `extractions.py`, `research.py`, `privacy.py`, `billing.py`, `admin.py`), keeping `api.py` as an application factory and router aggregator.
- **Consequences:** Massively improved maintainability, clear module ownership, reduced risk of merge conflicts, and easier unit testing of isolated route controllers.

### ADR-003: Distributed Tracing with OpenTelemetry
- **Status:** **Proposed**
- **Context:** Asynchronous document ingestion and extraction pass through Google Cloud Pub/Sub and dedicated worker processes. Correlating a failed extraction back to the originating HTTP upload request requires manual log searching by document ID.
- **Decision:** Standardize on OpenTelemetry instrumentation. Propagate W3C `traceparent` headers through Pub/Sub message attributes and database outbox records.
- **Consequences:** Provides unified distributed trace timelines across HTTP requests, background worker processing, and external Gemini API invocations.

### ADR-004: Multi-Model LLM Gateway Protocol
- **Status:** **Proposed**
- **Context:** System currently defaults to Google Gemini models (`gemini-2.0-flash`, `text-embedding-004`). Certain enterprise compliance mandates require data residency support or air-gapped on-premises fallback models.
- **Decision:** Formalize a multi-provider gateway conforming to the `TextGenerator` protocol, enabling runtime model selection based on query classification, cost thresholds, and tenant policies.
- **Consequences:** Shields the core retrieval and generation pipelines from provider lock-in while allowing tenants to choose between low-latency Flash models and high-reasoning frontier models.

---

*Document compiled from exhaustive architectural review of the KnowledgeForge AI platform codebase. All observations, schemas, and metrics reflect active repository state.*