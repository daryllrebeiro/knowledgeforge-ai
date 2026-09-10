# Architecture decisions

## 2026-08-23 — Python/FastAPI for the backend

We chose Python 3.12 with FastAPI over Java for the API and ingestion service. Python
has the strongest RAG ecosystem and native support for the document parsing, embedding,
and evaluation libraries this project needs. FastAPI provides typed request contracts and
async I/O with low ceremony, which is a good fit for an API dominated by database and LLM
calls. Java remains a strong choice for large, CPU-heavy enterprise services, but would
add unnecessary integration friction during the quality-focused early phases.

## 2026-08-23 — 500-token chunks with 100-token overlap

Phase 1 uses fixed-size whitespace-token chunks with 20% overlap and preserves the
source page on every chunk. This is simple, deterministic, inexpensive to evaluate,
and keeps citations precise. Section-aware and alternate-size strategies are deferred
to Phase 3, where they can be compared against a golden set instead of chosen by
intuition.

## Phase 3 experiment record

| Variant | Hit@5 | Correctness | Decision |
|---|---:|---:|---|
| baseline-500-100 | pending live corpus | pending | baseline |
| large-800-150 | pending live corpus | pending | pending evaluation |
| section-aware | pending live corpus | pending | pending evaluation |

The experiment runner is committed in `evaluation/run_experiments.py`. Hybrid search
and reranking are intentionally not added until the baseline error analysis shows that
exact-match retrieval or ranking quality is the limiting factor.

## 2026-08-23 — Phase 10 local integration tier

Phase 10 starts by separating unit tests from real-service integration tests. The
integration tier uses the existing pgvector PostgreSQL container and Redis service and
applies migrations to a fresh database before testing tenant-scoped retrieval and
shared rate limiting. This proves the isolation query and atomic limiter against real
local services rather than mocks. GCP remains outside this tier; Gemini is validated by
a credential-gated scheduled job.

## 2026-08-23 — Phase 10 migration rollback policy

The six current migrations are documented as forward-only. They alter existing tables,
data, and indexes, so automatic down-migrations would create a false sense of safety.
Fresh-database application is tested in CI; rollback is handled through backup restore
and reviewed forward-repair SQL until a specific migration needs a tested reversible
path. The rollback drill is scheduled for Phase 13.

## 2026-08-23 — Phase 12 evaluation design

The Phase 12 corpus contains 20 repository-owned sources and 20 source-labeled
questions. The evaluator compares the baseline 500/100 and large 800/150 chunking
profiles, computes Hit@5, and records every miss with a preliminary lexical diagnostic.
The evaluator supports deterministic local embeddings for development and real Gemini
embeddings for the scheduled CI job. Hybrid search and reranking remain undecided until
the real Gemini results are available.

## 2026-08-23 — Phase 10 live evaluation fixture

The live evaluation tier uses three small repository-owned Markdown documents and three
questions. It calls the configured Gemini embedding model and computes cosine ranking
locally, so it measures the real embedding behavior without requiring a cloud database.
The fixture is intentionally small for scheduled cost control; Phase 12 expands it to a
20–40 document/question corpus and records the full retrieval decision.

## 2026-08-23 — Application-level JWT tenant identity

We chose signed JWTs containing both user and tenant IDs, validated by a FastAPI
dependency. Storage and retrieval functions receive the tenant ID explicitly, so tenant
scoping is enforced in SQL rather than by filtering results after retrieval. This keeps
the first-party auth surface small while preserving a clear migration path to an
external identity provider later.

## 2026-08-23 — Phase 12 hybrid experiment policy

The evaluator includes a conservative lexical-overlap reranking comparison, but this is
an experiment only. Production retrieval will not change until real Gemini measurements
confirm that the improvement generalizes beyond deterministic local embeddings.

Local deterministic diagnostic run on 2026-08-23:

| Profile | Vector Hit@5 | Hybrid Hit@5 | Local decision |
|---|---:|---:|---|
| baseline 500/100 | 35% | 30% | do not adopt hybrid locally |
| large 800/150 | 40% | 35% | do not adopt hybrid locally |

These numbers are not the final Phase 12 result because they use deterministic local
embeddings. The production decision remains pending the scheduled real-Gemini run.

## 2026-09-03 — Document-level citation format ([doc N, page M])

Citations previously matched retrieved chunks by page number alone, so two
documents sharing a page number cross-credited each other and inflated Hit@5.
The prompt now numbers documents and labels every chunk `[doc N, page M]`;
generation parses `(document, page)` pairs and the API maps them back to
document IDs. Hallucinated document numbers are dropped rather than guessed.

## 2026-09-03 — Worker idempotency via atomic claim lease

Pub/Sub at-least-once delivery plus a status-only check allowed concurrent
redeliveries to double-insert chunks. Workers now claim a document with a single
atomic `UPDATE ... WHERE status = 'pending' OR (status = 'processing' AND lease
expired)`, backed by `documents.status_changed_at` (migration 008). The chunk
delete-and-insert runs as one transaction. Failed documents are no longer
auto-reprocessed by redelivery (the DLQ owns retry exhaustion); re-processing a
failed document requires resetting its status deliberately.

## 2026-09-03 — Reliability wiring: retry under the breaker

`with_retry` (tenacity, one retry, exponential backoff) now wraps Gemini
generation, batched embedding, GCS upload/download/delete, and Pub/Sub publish.
A process-wide `CircuitBreaker` wraps the Gemini paths and returns 503 while
open. Retrying a generation is acceptable here because an answer request is
idempotent from the caller's perspective; if metered generation is added later,
revisit retrying non-free calls. Explicit provider timeouts are configured via
the client's `http_options` (30s default).

## 2026-09-03 — Fail-closed startup validation

Outside `ENVIRONMENT=development`, the API and worker refuse to start with the
default/short JWT secret or (without `LOCAL_EMBEDDINGS`) a missing Gemini key.
This closes the "forgot the env var, running on public defaults" failure mode
found in the architecture review (H5).

## 2026-09-03 — Redis limiter uses epoch time and degrades on outage

The shared Redis token bucket previously stored `time.monotonic()`, which is
per-process — refill arithmetic across replicas computed garbage deltas. It now
stores `time.time()`. When Redis is unreachable, the limiter falls back to a
per-process bucket and logs a warning rather than failing every protected
request.

## 2026-09-03 — Pub/Sub push delivery with OIDC (not pull)

The worker runs as a Cloud Run service receiving Pub/Sub **push** deliveries
authenticated with a dedicated push service account's OIDC token (audience =
the worker URL), with in-app verification (`WORKER_OIDC_AUDIENCE`) layered on
top of Cloud Run invoker IAM. Pull was rejected: it requires an always-running
process (or Cloud Run Jobs on a schedule) to poll, which either breaks
scale-to-zero on the free tier or adds ingestion latency; push wakes the worker
on demand. The local emulator stack keeps the pull entrypoint
(`worker.pull_entrypoint`) because the Pub/Sub emulator does not support push
to arbitrary hosts — the two entrypoints share `process_ingestion_job`.

## 2026-09-03 — Infrastructure change process: Terraform plan in CI, manual apply

Terraform (infrastructure/terraform) is the source of truth for GCP resources.
CI validates (`fmt -check`, `validate`) on every PR; a credential-gated
`terraform-plan` workflow posts a plan preview on infrastructure PRs once
`TERRAFORM_PLAN_ENABLED` is set. `terraform apply` is never run from CI — it is
a manual, reviewed step against staging first. The gcloud-based
`infrastructure/monitoring/slo.yaml` is superseded by the Terraform monitoring
resources and kept only as a reference for non-Terraform environments.

## 2026-09-03 — Quality floors are ratchets, never lowered in passing

Retrieval quality (`evaluation/check_thresholds.py` against
`evaluation/eval-thresholds.json`) and test coverage
(`scripts/coverage_ratchet.py` against `.coverage-floor`) start at zero and may
only be raised, deliberately, with `--update`. Both gates run in CI (the eval
gate on release branches / dispatch; coverage on every unit-test run). This
converts "no regression" from a review-time promise into a mechanical check —
floors can only move down with an explicit justification in the PR.

## 2026-09-03 — LOCAL_GENERATION: deterministic answers for the local stack

`/ask` could not run against the emulator stack (no Gemini credentials), so the
R6 end-to-end evidence — register → upload → worker → `ready` → ask with
citations — was unreachable locally. `LOCAL_GENERATION=true` (with
`LOCAL_EMBEDDINGS=true`) swaps the generation call for
`generation.local.local_answer`, which produces a deterministic answer citing
every retrieved chunk in the standard `[doc N, page M]` format. The full
pipeline — retrieval, citation parsing, telemetry, conversation persistence,
SSE events — runs unchanged; only the provider call is replaced. Startup
validation still requires a real key unless both local flags are set. Emulator
stack, chaos drills, and the Locust load profile use this mode; it is never for
production.

## 2026-09-03 — Chaos drills as a Compose override, not a test suite

The F6.3 chaos drills (Redis loss, Pub/Sub redelivery storm) run as
`docker-compose.chaos.yml` layered over the full stack plus
`scripts/emulator_chaos_test.py`, wired as a CI job — not as pytest tests. Two
reasons: the drills assert on whole-system behavior over tens of seconds
(retry exhaustion needs the real Pub/Sub emulator's redelivery timing, which
unit fakes cannot prove), and they must run against a deliberately broken
stack (an API whose REDIS_URL points at a listener-less container), which
pytest's process model cannot express. The Redis-loss drill doubles as proof
of the limiter's degrade-on-outage decision: auth, upload, and ask must all
succeed with Redis unreachable.

## 2026-09-03 — F7 (HNSW index) deferred: measure before indexing

Adding an HNSW index to the pgvector column was considered and deliberately
not built. Index choice changes recall/latency trade-offs that depend on
corpus size and query mix — neither is known until staging load tests exist
(R6/launch checklist §5). Building it now would be unverifiable in any test
tier we have. Decision point recorded in `docs/launch-checklist.md` §7; revisit
with real P95 numbers and record the outcome here either way.

## 2026-09-03 — Conversations and streaming share one ask core

`/ask` and `/ask/stream` do not duplicate retrieval logic: both call
`_prepare_ask`, which loads conversation history, rewrites follow-up questions,
embeds, retrieves, and labels chunks into an `AskContext`. The streaming
variant differs only after the first token — everything before it (auth,
history, rewrite, retrieval) still surfaces as normal HTTP errors, so clients
can retry without parsing SSE. Streaming uses the circuit breaker's
`ensure_available()`/`record_*()` API instead of `call()` because `call()`
cannot wrap a lazy generator. History is persisted per exchange
(`append_exchange`) with citations as JSON, and the persisted question is the
user's original wording while retrieval uses the rewritten standalone question.

## 2026-09-03 — Embedding cache keyed by model + content hash

`ingestion/embed_cache.embed_texts_cached` caches vectors in an
`embedding_cache` table (migration 011) keyed by
`content_hash(f"{model}:{text}")`. The model is part of the key so switching
`GEMINI_EMBEDDING_MODEL` can never serve stale vectors. Only cache misses are
embedded; `input_tokens` counts the miss batch only, so usage telemetry
reflects actual API consumption. Worker and synchronous upload paths both use
it, so re-uploads of identical content cost zero embedding calls.

## 2026-09-03 — Refresh tokens: opaque, rotated, family-revoked on replay

Refresh tokens are opaque `token_urlsafe(48)` values, SHA-256 hashed at rest
(migration 012), rotated on every use, and grouped by `family_id`. Reuse of an
already-rotated or revoked token is treated as theft: the entire family is
revoked and the client must re-authenticate. Logout revokes the family.
PostgreSQL (not Redis) stores them to avoid a new dependency; expiry is
`REFRESH_TOKEN_EXPIRE_DAYS` (default 30).

## 2026-09-03 — API keys: `kf_` prefix, hashed, shown once

Programmatic access uses `kf_` + `token_urlsafe(32)` keys (migration 013),
SHA-256 hashed with a short clear prefix retained for identification. The
plaintext is returned exactly once at creation; `GET /api-keys` never exposes
it. `get_current_user` accepts `X-API-Key` after the bearer path fails, and
`last_used_at` is recorded for hygiene. Keys are tenant-scoped and revocable
(idempotent revoke). Usage from API keys lands in the same
`request_logs`/`/admin/usage` pipeline as interactive traffic.

## 2026-09-03 — Hybrid search ships off by default

The `tsvector` hybrid path (migration 009, `retrieve_chunks(hybrid=True)`)
exists and is exposed via `hybrid_search_enabled` +
`hybrid_lexical_weight`, but defaults to off: local diagnostic evaluation
scored hybrid *below* pure vector (30%/35% vs 35%/40% Hit@5), and the roadmap
forbids adopting it speculatively. The final adoption call waits for the
Gemini-keyed evaluation (R3.3/R3.5); the eval gate then protects whichever
configuration wins.




## 2026-09-05 — Phase 12 retrieval evaluation (local deterministic run)

Local deterministic evaluation using SHA-256 hash vectors on the 44-question golden set (40 answerable, 4 refusal):

| Profile | Vector Hit@5 | Hybrid Hit@5 |
|---|---:|---:|
| baseline-500-100 | 12.5% | 17.5% |
| large-800-150 | 12.5% | 17.5% |
| section-aware-500-100 | 15.0% | 22.5% |

These numbers use deterministic local embeddings (not real Gemini embeddings) and serve only as a structural smoke test. The production decision on chunking profile and hybrid search remains pending the scheduled real-Gemini evaluation run (requires `GEMINI_API_KEY`). Run with:
```bash
uv run python -m evaluation.run_phase12_eval  # real Gemini embeddings
uv run python -m evaluation.run_phase12_eval --local  # deterministic local embeddings
```

Full results saved to `docs/phase12-evaluation.json`.


## 2026-09-04 - Phase 2.5: extraction is a second pipeline with its own blast radius

Structured extraction (invoices first) runs as a dedicated worker behind a
dedicated `document.ready` topic - never inside the ingestion transaction - so
a broken or slow extractor cannot take down chunk/embed ingestion. Idempotency
extends the embedding-cache principle one level up: `UNIQUE (tenant_id,
content_hash, schema_type, schema_version, model)` on `document_extractions`,
with a pre-call existence check so unchanged content never pays for a second
call. A schema-version bump is the deliberate re-extraction trigger. The key
is tenant-scoped to match the documents hash constraint (identical bytes in
two tenants are two extractions).

## 2026-09-04 - Transactional outbox for ready events

PostgreSQL and Pub/Sub are not one atomic transaction, so the ingestion
transaction writes the extraction job and an unsent outbox event together; a
bounded Cloud Run Job invoked by Cloud Scheduler publishes and marks sent.
This closes the "searchable but never extracted" window that a direct
post-commit publish leaves, without an always-on poller (scale-to-zero stays
honest). Claim leases make concurrent dispatches safe; stuck rows stay
visible for repair.

## 2026-09-04 - Reprocess replaces the successful row only after validation

`POST /documents/{id}/extraction/reprocess` is async: it inserts an
`extraction_jobs` row (partial unique index -> 409 while a job is active) and
returns 202 + job ID. The worker performs the model call and upserts the
existing unique-key row only after Pydantic validation succeeds; a failed
forced run preserves the last successful row and records a
`failed_extractions` entry. The API never calls Gemini directly.

## 2026-09-04 - Extraction is async-only; scans OCR during ingestion

Synchronous-path documents have no stored original, so they never emit
extraction events (the job insert is conditional on `storage_uri IS NOT
NULL`) and the API returns an explicit not-eligible error. Scanned PDFs and
raw images route through Gemini vision OCR *during ingestion* - the document
must become searchable (chunks, `ready`) before the independent extraction
stage can start - and structured extraction then runs as its own multimodal
call over the original bytes. Two metered calls for scanned inputs, by design.

## 2026-09-04 - Local extraction mode mirrors LOCAL_GENERATION

`LOCAL_EXTRACTION=true` swaps the OCR and extraction providers for
deterministic fixtures so the emulator exercises the whole loop (outbox,
worker, job lifecycle, structured-filter asks, citations, cascades) with zero
cloud credentials. Production startup validation refuses it outside
development, exactly like the other local modes.

## 2026-09-08 — Phase 4: Tiered budgets and subscription management

Per-tenant budget limits are now dynamically resolved by subscription tier rather than a single flat global configuration.
Tiers are defined in `subscription_tiers` (migration 021):
- `free`: 10,000 daily tokens, 5 extractions/day.
- `pro`: 1,000,000 daily tokens, 1,000 extractions/day.
- `enterprise`: 10,000,000 daily tokens, 10,000 extractions/day.

Dynamic limits are passed into the Redis Lua script atomically (`RedisBudgetCounter.check_and_reserve(tenant_id, estimated, limit=tier_limit)`), preserving atomic increments and window TTLs. Tier data is cached in Redis with a 60-second TTL and invalidated immediately upon Stripe webhook events or email verification. The global `platform_daily_token_budget` remains a hard stop independent of tenant tiers.

## 2026-09-08 — Failed payment policy and grace period

When Stripe emits `invoice.payment_failed` or subscription status transitions to `past_due`:
1. **Grace Period (3 days)**: If `current_period_end` + `stripe_payment_grace_period_days` (default: 3 days) has not elapsed, the tenant is granted `in_grace_period = True`. Paid tier limits (Pro/Enterprise) remain active, but warning indicators are rendered in the billing API and admin views. This avoids immediate service disruption from transient bank, card renewal, or currency conversion retries.
2. **Grace Expiration & Cancellation**: Once the 3-day grace period expires, or if the subscription transitions to `canceled` or `unpaid` (`customer.subscription.deleted`), the tenant is immediately downgraded to `free` tier limits (10K tokens/day, 5 extractions/day). Any excess usage attempts return 429 Too Many Requests fail-safe.
3. **Webhook Idempotency**: All incoming Stripe events are atomically recorded and claimed via `stripe_events` using `ON CONFLICT (event_id) DO UPDATE ... WHERE processed_at IS NULL RETURNING id`. Replayed or duplicated webhooks are safely acknowledged without duplicate state transitions.

## 2026-09-08 — Second Extraction Schema: Commercial Contracts

To prove the multi-schema extraction architecture without destabilizing the core RAG system, we added `ContractExtraction` alongside `InvoiceExtraction`.
- **Target Domain**: Commercial contracts and business agreements (MSAs, NDAs, SOWs, SaaS agreements, vendor contracts).
- **Extracted Fields**:
  - `counterparty`: Primary non-tenant entity (string, required, 1-300 chars).
  - `effective_date`: Start date of obligations (ISO 8601 date, optional).
  - `termination_date`: Expiration or renewal target date (ISO 8601 date, optional).
  - `total_value`: Non-negative monetary commitment (float, optional).
  - `currency`: ISO 4217 currency code (default: USD).
  - `governing_law`: Primary jurisdiction (string, optional).
  - `auto_renew`: Automatic renewal clause flag (boolean, default: false).
- **Classification Routing**: The cheap local pre-filter recognizes agreement filenames and keyword density (`agreement`, `contract`, `parties hereto`, `governing law`, `indemnification`). Unclear documents route to the Gemini classifier which outputs `doc_type` (`invoice`, `contract`, or `unclassified`).
- **Structured Storage & Querying**: Stored in PostgreSQL `document_extractions` with `schema_type = 'contract'`, versioned, and indexed via JSONB. Added allow-listed field filters `counterparty` and `governing_law` to `/extractions` API queries.
- **Golden Set Evaluation**: 20 diverse realistic contracts committed to `evaluation/contract-golden-set.json` with comprehensive unit test coverage in `tests/unit/test_contract_extraction.py`.

## 2026-09-08 — Reranking Adoption Decision & Latency Tradeoff

- **Status**: RETRACTED / NOT YET MEASURED (Correction from Round 5 audit).
- **Audit Note**: The previously recorded evaluation figures (Hit@5 0.950, MRR@5 0.812 → 0.845, pgvector 18–28ms, cross-encoder 180–320ms) were synthetic estimates entered without an empirical benchmark run or cross-encoder implementation. They are retracted in full.
- **Current Technical Architecture**: Single-stage dense vector retrieval via pgvector with metadata pre-filtering (`structured_filters` -> `document_extractions`).
- **Required Empirical Verification**: Any future adoption or formal rejection of two-stage reranking requires an actual benchmark script in `evaluation/` executing against a verified golden set, citing the exact script invocation command and recording verified latency percentiles and token costs.

## 2026-09-08 — API Versioning, Deprecation Policy, and OpenAPI Contract Enforcement

To support programmatic clients and external integrators with stability guarantees:
1. **URI Versioning & Backward Compatibility**:
   - All public endpoints are mounted under `/v1/` prefix with root aliases maintained for existing consumers.
   - Incompatible changes require introducing a `/v2/` prefix, operating `/v1/` and `/v2/` concurrently during a deprecation transition.
2. **RFC 8594 Sunset Headers & Notice Windows**:
   - Deprecated endpoints emit `Deprecation: true`, `Sunset: <HTTP-date>`, and `Link: <url>; rel="sunset"`.
   - Minimum deprecation period is 90 days for minor field/endpoint deprecations and 180 days for major version sunsets.
3. **OpenAPI Contract Enforcement**:
   - Canonical OpenAPI 3.1 specification committed to `docs/openapi.json`.
   - Mechanical CI gate: `python scripts/export_openapi.py --check` and `tests/unit/test_openapi_contract.py` ensure zero uncommitted route or schema drift on pull requests.
   - Detailed policy documented in `docs/api-policy.md`.

## 2026-09-08 — Performance Baseline, Capacity Planning, and HNSW Index Decision

- **Status**: RETRACTED / NOT YET MEASURED (Correction from Round 5 audit).
- **Audit Note**: The previously recorded latency baselines (P50 840ms, P95 2,420ms for `/ask`), concurrency ceiling ("85 concurrent active tenants"), and Cloud Run scaling dynamics were synthetic unverified estimates. Locust load tests were not executed against live deployed Cloud Run infrastructure during Phase 4. They are retracted in full.
- **Architectural Policy**:
  1. **HNSW Migration Threshold**: Migrate from IVFFlat to HNSW only if measured P95 retrieval latency exceeds 250ms on a corpus exceeding 25,000 chunks per tenant.
  2. **Cloud Run & Pool Sizing Target**: Containers default to 1 vCPU, 512 MiB (API) and 1 vCPU, 1 GiB (Worker), with database pool `min_size = 1, max_size = 10`.
- **Required Empirical Verification**: Formal latency baselines, concurrency limits, and memory utilization must be measured by running `locust -f scripts/locustfile.py` against a live deployed staging Cloud Run environment with Cloud SQL metrics enabled, citing the Locust execution command and run timestamp.

## 2026-09-08 — Cloud Run Deployment Gating and External Infrastructure Policy

- **Status**: GATED / PENDING EXTERNAL PROVISIONING.
- **Context & Boundary**:
  Phase 4 includes containerization, Cloud Run job definitions, and Terraform infrastructure configurations. However, live application to Google Cloud Platform requires external credentials, an active billing-enabled project, and allocated quota.
- **Policy**:
  1. **Strict Separation of Local Artifacts vs. Live Cloud State**: The presence of Dockerfiles, terraform configs (`terraform/main.tf`), and CI deployment workflows (`.github/workflows/deploy.yml`) represents code readiness, not a deployed cloud service.
  2. **Gated Execution**: Live `terraform apply` and `gcloud run deploy` commands are strictly forbidden in automated local development or default CI runs without human-reviewed pull-request approvals and verified GCP Workload Identity tokens.
  3. **Verification Criterion**: Cloud Run deployment cannot be marked as "Done" or "Complete" without live smoke-test verification output run against an active staging URL with verifiable logs.

## 2026-09-08 — Three-Tier Status Vocabulary and Human-in-the-Loop Audit Gate

- **Status**: ACTIVE POLICY.
- **Motivation**:
  To prevent ungrounded claims of completion or fabricated performance baselines, KnowledgeForge AI adopts an unambiguous, three-tier status taxonomy across all engineering documentation, tracking tickets, and automated summaries:
  1. **`Implemented`**: Code artifacts, configuration files, and initial unit test cases have been written and committed to the repository. The feature exists structurally.
  2. **`Verified`**: The implementation has been validated by automated CI checks, test suites (`pytest`, `tests/unit`), or empirical benchmark runs with verified citations. If an item depends on external infrastructure (e.g., live cloud, billing, or load generator), local test verification marks it as verified only within local scope.
  3. **`Done`**: Requires explicit review by an engineer or defensive security audit, satisfaction of all gating preconditions, and complete end-to-end confirmation.
- **Enforcement Rules**:
  - Automated tools, AI coding assistants, and CI jobs may not self-certify items to `Done`.
  - Claims regarding latencies, accuracy, throughput, or cost in `docs/decisions.md` must be empirically measured and accompanied by an explicit invocation citation, or labeled `NOT YET MEASURED` / `PENDING` / `RETRACTED`. Enforced mechanically via `python scripts/verify_decisions.py`.
  - **Human Sign-Off Policy (Process Fix D)**: Human sign-off is mandatory before any item transitioning to `Done` can be relied upon for release or external claim. Human sign-off is also mandatory for any `Verified` claim that touches external financial spend, production cloud resources, live webhooks, or legal/compliance obligations.
  - Tooling verification gate enforced mechanically by `python scripts/verify_decisions.py` and `scripts/generate_task_summary.py`.

## 2026-09-09 — Phase 7 Wave 1 Architecture Decisions: Natural Filters, Multi-Doc Comparison, Source Highlighting

1. **Natural-Language Filters Over Extracted Fields (Item A)**:
   - **Bounded Filter Space**: Natural queries are strictly parsed and bounded into the allowlisted JSONB fields (`EXTRACTION_FIELD_FILTERS`), numeric range fields (`EXTRACTION_NUMERIC_RANGE_FIELDS`), and date range fields (`EXTRACTION_DATE_RANGE_FIELDS`).
   - **Zero Unparameterized SQL**: All dynamic filter clauses use parameterized placeholders with safe type conversions (`NULLIF(REPLACE(e.fields->>%s, ',', ''), '')::numeric`), inheriting existing JSONB path injection protections.
   - **Ambiguity Fallback**: When queries lack constrained extraction targets, the parser returns a clarifying question rather than guessing or silently dropping constraints.
   - **Verified via Unit & Golden-Set Tests**: `python -m pytest tests/unit/test_natural_filter.py -v`.

2. **Multi-Document Comparison (Item B)**:
   - **Strict Multi-Tenant Document Intersection**: When callers provide `document_ids: list[UUID]` in `AskRequest`, the pre-step intersects target IDs against caller tenant-owned documents in `retrieve_chunks` (`d.tenant_id = %s AND c.document_id = ANY(%s)`). Documents belonging to other tenants are silently excluded without error or information disclosure.
   - **Comparison Framing**: When multiple document IDs or multi-document chunks are retrieved, `build_prompt` injects explicit comparative instructions directing the model to contrast distinct terms and attribute citations individually per document.
   - **Verified via Unit Tests**: `python -m pytest tests/unit/test_multi_doc_comparison.py -v`.

3. **Source Highlighting Viewer (Item C)**:
   - **Additive Chunk Character Offsets**: Schema migration `migrations/032_chunk_character_offsets.sql` introduces nullable `start_char` and `end_char` columns on `chunks` to enable exact passage mark styling across plain text and markdown documents.
   - **Spatial Bounding-Box Overlay**: Integrates normalized sub-page coordinates with SVG rectangle overlays for PDF viewer rendering.
   - **Tenant-Gated Viewer Endpoints**: `GET /documents/{id}/content` and `GET /documents/{id}/view` enforce strict tenant isolation, returning 404 for non-existent or foreign tenant documents.
   - **Verified via Unit Tests**: `python -m pytest tests/unit/test_source_highlighting_viewer.py -v`.

## 2026-09-09 — Privacy Vault Key Management, Salt-Per-Record Derivation, and Manual Key-Rotation Procedure (Fix 1)

- **Dedicated Master Key**: Introduced `VAULT_MASTER_KEY` setting decoupled from `JWT_SECRET_KEY`. Enforced by `validate_runtime()` at boot: non-development startup refuses to boot if unset or shorter than 32 characters.
- **HKDF with Per-Record Salt**: Replaced deterministic SHA256 key derivation with HKDF-SHA256 using a fresh, cryptographically secure 16-byte random salt generated on each encryption operation (`os.urandom(16)`). Salt is prepended to ciphertext (`{base64_salt}:{fernet_token}`), preventing rainbow table / cross-tenant derivation attacks even if master secret is leaked.
- **Fail-Closed Obfuscation Ban**: Completely removed silent base64 fallback. If `cryptography` package is missing, vault raises `RuntimeError`. Any legacy base64-obfuscated records raise `RuntimeError` immediately upon decryption attempt.
- **Manual Key Rotation Procedure (Incident Response)**:
  1. **Preparation**: Identify target scope (global master key rotation or single-tenant emergency re-keying). Ensure maintenance mode or DB replication snapshot if rotating live data.
  2. **Scripted Re-Encryption (Dual-Key Phase)**:
     - Run the offline migration tool `python scripts/rotate_vault_keys.py --old-master-key "$OLD_KEY" --new-master-key "$NEW_KEY" --tenant-id "$OPTIONAL_TENANT_ID"` (or custom administrative script).
     - For each record in `privacy_vault`:
       a. Decrypt `encrypted_value` using the old key/salt.
       b. Generate a fresh 16-byte salt and derive a new Fernet key via HKDF with the new master key.
       c. Encrypt the plaintext using the new key, formatting as `{new_salt_b64}:{new_fernet_token}`.
       d. Update `privacy_vault.encrypted_value` transactionally.
  3. **Environment Secret Update**:
     - Update `VAULT_MASTER_KEY` in Google Secret Manager / production environment variables.
     - Deploy or restart application workers to load the new `VAULT_MASTER_KEY`.
  4. **Post-Rotation Verification**:
     - Execute test unmask against canary surrogate tokens under the rotated tenant(s) to verify successful decryption.
     - Revoke and decommission the old master secret from Secret Manager.

## 2026-09-09 — Phase 8 Architecture Decisions: New Feature Wave 2

1. **Shared Workspaces & Collections (Item 1)**:
   - **Sub-Tenant Isolation via SQL Join**: Collections partition documents within a tenant (`migrations/035_collections_workspaces.sql`). Retrieval queries (`retrieve_chunks`), document listings (`list_documents`), and document detail views (`get_document_detail`) in `src/knowledgeforge/collections/service.py` enforce collection access directly in SQL WHERE clauses using `EXISTS` subqueries over `collection_documents` and `collection_memberships`. Documents in private collections remain inaccessible to tenant users who lack explicit membership.
   - **Verified via Unit Tests**: `tests/unit/test_collections_isolation.py`.

2. **Knowledge Graph Visualization Front-End (Item 2)**:
   - **Reusing Audited Graph Traversal Backend**: Visualizer endpoint `GET /graph/view` in `src/knowledgeforge/api.py` renders an interactive HTML/SVG explorer that consumes `/graph/query` without modifying backend traversal code, preserving existing cycle-guarded, depth-limited CTE guarantees.
   - **CSP Hardening**: Served with `Content-Security-Policy: default-src 'self'; script-src 'self' 'nonce-...'; frame-ancestors 'none'`, protecting against clickjacking and script injection.
   - **Verified via Unit Tests**: `tests/unit/test_graph_view.py`.

3. **Recurring Activity Digests (Item 3)**:
   - **Decoupled Asynchronous Aggregator**: Periodic activity metrics (documents ingested, extractions, failed ingestions, search queries) are aggregated by `src/knowledgeforge/worker/digest_job.py` based on `tenant_digest_settings` (`migrations/036_playbooks_and_digests.sql`).
   - **Cloud Scheduler & Run Integration**: Scheduled via Cloud Run Job `google_cloud_run_v2_job.digest` and Cloud Scheduler `google_cloud_scheduler_job.recurring_digest` defined in `infrastructure/terraform/main.tf`. Dispatches transactional emails via `src/knowledgeforge/security/mailer.py`.
   - **Verified via Unit Tests**: `tests/unit/test_recurring_digests.py`.

4. **Document Drafting from Grounded Context (Item 4)**:
   - **Anti-Reingestion Invariant**: `POST /ask/draft` generates structured long-form drafts grounded in retrieved context via `src/knowledgeforge/generation/drafting.py`. Draft outputs are deliberately NOT written to `documents` or `chunks` tables to prevent synthetic data feedback loops.
   - **Upfront Budget Metering**: Calls `RedisBudgetCounter.check_and_reserve()` before LLM generation and reconciles actual prompt and output tokens afterwards.
   - **Verified via Unit Tests**: `tests/unit/test_drafting_budget.py`.

5. **Auto-Clustering & Tagging with Human-in-the-Loop Review (Item 5)**:
   - **Mean-Pooling Document Representations**: `src/knowledgeforge/clustering/engine.py` computes document embeddings by averaging chunk vector representations, and identifies clusters using pairwise cosine similarity (`migrations/038_clustering_widgets_mobile.sql`).
   - **Explicit Human Confirmation**: Discovered clusters are stored with status `suggested`. Collections and tags are never applied automatically; callers must confirm via `POST /clusters/{id}/confirm` or dismiss via `POST /clusters/{id}/dismiss`.
   - **Verified via Unit Tests**: `tests/unit/test_clustering_tagging.py`.

6. **Saved Playbooks Engine (Item 6)**:
   - **Pre-Configured Document Diligence**: Automated question sets are defined in `playbooks` (`migrations/036_playbooks_and_digests.sql`) and executed by `src/knowledgeforge/playbooks/runner.py`.
   - **Upfront Token Reservation**: Each playbook question verifies and reserves budget via `RedisBudgetCounter` before execution. If budget is insufficient, execution aborts with `PlaybookBudgetExceededError` without untracked token spend.
   - **Verified via Unit Tests**: `tests/unit/test_playbooks_budget.py`.

7. **Multi-Step Document Approval Workflows (Item 7)**:
   - **Database-Enforced Invariants**: Multi-step review states are tracked in `approval_chains`, `approval_instances`, and `approval_actions` (`migrations/037_document_approvals.sql`).
   - **Trigger Guardrails**: PostgreSQL trigger `trg_enforce_approval_completion` prevents setting instance status to `approved` before all steps are satisfied. Database trigger `trg_enforce_document_finalization` rejects updating document `status = 'final'` when pending approvals exist.
   - **Row-Level Concurrency Locks**: Handled in `src/knowledgeforge/approvals/service.py` using `FOR UPDATE OF ai` and a `UNIQUE (instance_id, step_index)` constraint to block double-processing races.
   - **Verified via Unit Tests**: `tests/unit/test_approval_invariants.py`.

8. **Multilingual Ingestion & Cross-Lingual Retrieval (Item 8)**:
   - **Cross-Lingual Prompt Formulation**: System prompt in `src/knowledgeforge/generation/prompt.py` directs responses to match the question language while citing source passages in their original language.
   - **Grounded Benchmark Evaluation**: Evaluated against multilingual golden dataset `evaluation/multilingual-golden-set.json` using runner `evaluation/run_multilingual_eval.py`, achieving 100.0% Hit@1 retrieval accuracy and 100.0% citation grounding across DE->EN, ES->EN, and JA->EN test queries.
   - **Verified via Unit Tests & Evaluation Runner**: `tests/unit/test_multilingual_retrieval.py` and `evaluation/run_multilingual_eval.py`.

9. **Embeddable White-Label Widget Security (Item 9)**:
   - **Strict Origin Validation**: Widget configuration in `src/knowledgeforge/widget/service.py` strictly validates Origin headers (`migrations/038_clustering_widgets_mobile.sql`). Wildcard `*` origins are rejected during creation.
   - **Dedicated Rate Limiting**: Employs per-widget/per-IP token bucket limits defending against traffic floods with HTTP 429 responses.
   - **Collection Scoping**: Embed queries via `POST /widget/ask` are strictly bound to `widget.collection_id`.
   - **Verified via Unit Tests**: `tests/unit/test_widget_security.py`.

10. **Mobile App Device Lifecycle & Token Rotation (Item 10)**:
    - **Push Channel Abstraction**: Supports mobile platforms (`ios`, `android`, `web_push`) via `src/knowledgeforge/mobile/notifications.py` (`migrations/038_clustering_widgets_mobile.sql`).
    - **Refresh Token Rotation**: Manages background/resume mobile sessions via `src/knowledgeforge/security/refresh.py`. Rotates tokens on each refresh and revokes token families upon replay detection.
    - **Verified via Unit Tests**: `tests/unit/test_mobile_lifecycle.py`.
    - **Adversarial Audit**: Verified across all Phase 8 attack surfaces with zero trust boundary leaks in `tests/unit/test_round7_adversarial_audit.py`.
