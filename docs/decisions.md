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
