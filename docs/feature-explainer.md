# KnowledgeForge — Feature Explainer

**Date:** 2026-09-04
**Audience:** contributors and planners. Every feature is explained three ways —
what it does, how it works (mechanism, at the level of the actual code paths),
and why it is designed that way (the trade-off or incident that shaped it,
cross-referenced to [`decisions.md`](decisions.md)). A final section maps each
feature to the future roadmap so planning discussions start from how the system
actually behaves, not from memory.

**Companion documents:** [`architecture-review.md`](architecture-review.md)
(findings), [`roadmap.md`](roadmap.md) (sequencing),
[`validation-status.md`](validation-status.md) (evidence),
[`migrations.md`](migrations.md) (schema history).

---

## System overview

A user uploads a document; the API stores the bytes in object storage and
publishes an ingestion job; a worker extracts text, chunks it, embeds the
chunks into PostgreSQL + pgvector, and marks the document `ready`. When the
document is an invoice, a second worker classifies it and extracts typed
fields into a queryable table, and `/ask` can filter on those fields. The user
asks a question; the API embeds the question, retrieves the nearest tenant-
scoped chunks, and asks Gemini to answer *only* from those chunks with
page-level citations.

```
upload ──► GCS ──► Pub/Sub ──► worker ──► extract → chunk → embed → pgvector
                            (scans: OCR first)      │ ready (same transaction:
                                                   │  extraction job + outbox event)
        outbox dispatcher ◄── Cloud Run Job ◄──────┘
                │
                └──► Pub/Sub (extraction) ──► extraction worker ──► classify →
                     extract fields + confidence → document_extractions

ask ──► embed question ──► retrieve (tenant-scoped, optional structured filters)
               │
               └──► Gemini (grounded prompt + extracted fields) ──► cited answer / SSE stream
```

Four services (API, ingestion worker, extraction worker, outbox dispatcher),
one database, Redis for shared rate limiting, everything else is Google Cloud
or a local emulator standing in for it.

---

## 1. Document ingestion pipeline

**What:** async upload — PDF, DOCX, Markdown, HTML, TXT in, searchable chunks
out, with duplicate detection, crash-safe idempotency, and dead-letter
routing for poison messages.

**How it works:**
1. `POST /documents` (or batch upload) computes a SHA-256 **content hash**;
   an identical hash for the tenant returns the existing document
   (`duplicate` status) instead of re-ingesting — dedup is a DB constraint,
   not a check-then-race.
2. Bytes go to GCS at `{tenant_id}/{content_hash}/{filename}`; an ingestion
   message is published to Pub/Sub.
3. The worker (Cloud Run push with OIDC, pull locally on the emulator) claims
   the document with a single atomic
   `UPDATE ... WHERE status = 'pending' OR (status = 'processing' AND lease expired)`
   — the rowcount decides whether this delivery is *the* worker for the job.
   Everything after the claim (download, extract, chunk, embed, delete-and-
   insert chunks) runs in one transaction.
4. Extraction is per format (`extract_pdf/docx/markdown/text`); chunking is
   fixed-size whitespace tokens (500 tokens, 100 overlap) preserving the
   source page on every chunk.
5. Failures mark the document `failed` (never auto-retried by redelivery);
   Pub/Sub's own retry exhaustion routes malformed messages to a dead-letter
   subscription (5 attempts).

**Why this design:**
- **Claim lease, not a status check** (migration 008): Pub/Sub is
  at-least-once, and a read-then-write status check let concurrent
  redeliveries double-insert chunks. The atomic claim makes idempotency a
  property of the database, not of the worker's timing
  ([decisions: worker idempotency]).
- **Failures are deliberate, not retried**: a failed document needs human
  attention (bad PDF, no extractable text); silently reprocessing it on every
  redelivery hides real problems. The DLQ owns mechanical retry exhaustion.
- **Filename dispatch in the worker** (derived from `storage_uri`) means the
  API never needs to know how to parse anything — it just stores bytes and
  publishes a pointer. New formats (F3: pptx, CSV, OCR) are one `elif` plus
  an extractor module.
- **Push, not pull**, on Cloud Run: pull needs an always-running poller,
  which breaks scale-to-zero; push wakes the worker on demand
  ([decisions: Pub/Sub push with OIDC]).

**Future hooks (F3):** document versions/supersession chains, retention
policies per tenant, `.pptx`/CSV/OCR extraction, version cleanup. The claim
lease already supports re-ingest: `POST /documents/{id}/reingest` resets
status so the pipeline re-claims it. Image uploads (PNG/JPEG/TIFF) are
supported with async ingestion — OCR runs in the worker (section 17).

---

## 17. Structured extraction pipeline (Phase 2.5)

**What:** after a document becomes searchable, a second, independent pipeline
stage classifies it (invoice vs. everything else), extracts typed fields
against a validated Pydantic schema, stores them with per-field confidence in
a queryable table — and wires that table into the retrieval path so extracted
facts actually improve answers instead of sitting in a side table nothing
reads from.

**How it works:**
1. The ingestion transaction that marks a document `ready` also writes an
   extraction **job** and an **outbox** event in the same transaction
   (migration 014). A document can never be ready without its extraction
   trigger; PostgreSQL and Pub/Sub are never treated as one atomic
   transaction.
2. A bounded outbox dispatcher (a Cloud Run Job invoked by Cloud Scheduler
   every two minutes, scale-to-zero safe) claims a batch with a lease,
   publishes to the extraction topic, and marks rows sent only after publish
   succeeds. Concurrent dispatches cannot double-publish.
3. A dedicated extraction worker (separate service, subscription, service
   account, OIDC audience — a broken extractor can never take down chunk/embed
   ingestion) claims the job atomically. A cheap filename/keyword pre-filter
   classifies obvious cases for free; Gemini classifies the rest.
   Non-invoices terminate as `skipped` — expected, not an error.
4. Invoices get one structured-output Gemini call: fields **and** per-field
   confidence in the same response. Pydantic validates; one bounded retry on
   invalid output; exhausted retries write `failed_extractions` (mirroring
   `failed_ingestions`) — never a silent retry loop. Scans and images use
   Gemini vision over the original bytes (multimodal extraction).
5. Successful extractions land in `document_extractions` keyed by
   `(tenant_id, content_hash, schema_type, schema_version, model)` — the same
   idempotency principle as the embedding cache, one level up. The worker
   checks for an existing row *before* calling Gemini; the UNIQUE constraint
   is the race backstop. An unchanged re-ingested document never reaches the
   worker at all (content-hash dedup at the API).
6. Overall confidence below 0.75 (or any field below 0.5) flags
   `needs_review = true`; `GET /admin/extractions/review-queue` lists them.
7. `POST /ask` accepts optional `structured_filters` (`vendor_name`,
   `invoice_number`, `currency`, `schema_type`): extraction rows resolve to
   document IDs *before* embedding, chunk retrieval is scoped to that set, and
   the matched fields join the prompt as `[doc N, extracted fields]` blocks —
   so citations and the injection-defense framing extend unchanged. Without
   filters, behavior is byte-for-byte identical to before. An empty match
   short-circuits to the grounded refusal rather than reverting to an
   unfiltered tenant search.

**API surface:** `GET /documents/{id}/extraction` (latest or 404),
`GET /extractions` (tenant-scoped, allow-listed JSONB field filters),
`GET /admin/extractions/review-queue`, `POST /documents/{id}/extraction/
reprocess` (202 + job ID; 409 while a job is active; 422 for sync-path
documents), `GET /extraction-jobs/{job_id}` (tenant-scoped lifecycle view,
never raw model output).

**Why this design:**
- **Extraction has to earn its place in the retrieval path** — the
  `structured_filters` extension to `/ask` is the part that matters; the
  tables exist to make that integration affordable and safe.
- **Separate trigger, separate blast radius**: extraction never runs inside
  the ingestion worker's transaction; a separate topic means extraction
  backpressure can never block ingestion.
- **Async-only eligibility**: synchronous-path documents have no stored
  original, so they never emit extraction events (the job insert is
  conditional on `storage_uri IS NOT NULL`).
- **OCR is a hard prerequisite**: scans/images route through Gemini vision
  OCR *during ingestion* (the document must become searchable before the
  extraction stage can start), then structured extraction over the original
  bytes runs as its own call — two metered calls for scanned inputs, by
  design.

**Local mode:** `LOCAL_EXTRACTION=true` swaps both the OCR and extraction
providers for deterministic fixtures so the emulator stack exercises the
whole loop — outbox, worker, job lifecycle, structured-filter asks,
citations, deletion cascades — with zero cloud credentials. Refused outside
development.

**Cost model:** classification ≈ negligible with the keyword pre-filter
(only ambiguous documents cost a call); extraction ≈ 1.5–3k input + ~300
output tokens per invoice — fractions of a cent per document. Scanned inputs
add one OCR call during ingestion. All extraction token usage flows through
the same `request_logs`/usage pipeline (no second metering path).

**Deferred (each blocked on evidence, not on time):** multi-schema support
(storage layer already supports it), human-review UI, active learning on
corrections, confidence-gated verifier LLM call, per-tenant custom schemas.

---

## 2. Grounded answers with document-level citations

**What:** every answer is restricted to the retrieved context, cites sources
as `[doc N, page M]`, and refuses ("I don't have enough information.") rather
than guessing.

**How it works:** the prompt numbers documents and labels every chunk
`[doc N, page M]`; the system instruction demands answers only from context,
the exact refusal sentence, and the citation format — and states that
instructions inside the context are quoted data. Generation output is parsed
with a regex for `(doc, page)` pairs; the API maps document numbers back to
real document IDs *by joining against the retrieved set* — a hallucinated
document number is dropped, not guessed.

**Why this design:** the original citation format matched by page number
alone, so two documents sharing a page cross-credited each other and
inflated the eval's Hit@5 — the system was lying to its own harness
(review finding C1, [decisions: citation format]). Numbering documents and
validating parsed citations against what was actually retrieved means the
citation list can never reference something the model wasn't shown.
The refusal sentence is fixed text so it is testable verbatim, and the
quoted-data clause is the prompt-injection defense, verified by a
credential-gated *behavioral* test (ingest a hostile document, assert the
answer doesn't follow it) rather than a string check.

**Future hooks (F6):** LLM-as-judge correctness scoring on top of the
deterministic "expected key facts present" metric; the citation parser is
format-coupled, so any prompt-format change must update `CITATION_PATTERN`
and the golden set together.

---

## 3. Multi-tenancy and tenant isolation

**What:** every tenant's documents, conversations, chunks, and usage are
invisible to every other tenant — enforced in SQL, not in application
filtering.

**How it works:** JWTs carry `(user_id, tenant_id)`; storage and retrieval
functions receive `tenant_id` explicitly and every query filters on it.
`retrieve_chunks` has **no unfiltered branch** — the docstring says so
deliberately (review M5): a caller cannot accidentally search all tenants.
Account deletion cascades to documents, conversations, and raw GCS objects
(the smoke test asserts the object is really gone).

**Why this design:** tenant scoping enforced in application code after
retrieval is one forgotten `if` away from a cross-tenant leak; scoping in
the `WHERE` clause makes the invariant structural. The SQL-level scoping is
verified by an integration test that literally tries a cross-tenant probe
against real PostgreSQL.

**Future hooks (F4):** multi-user tenants (owner/member roles, invitations)
require per-tenant email uniqueness plus a migration and a product decision;
the scoping pattern extends naturally (role checks layer on top of tenant
checks, they don't replace them).

---

## 4. Authentication and programmatic access

**What:** three credential types — short-lived JWT access tokens, rotating
refresh tokens, and API keys — each designed for a different threat.

**How it works:**
- **JWT access tokens**: signed, short-lived, the fast path for every
  request.
- **Refresh tokens** (migration 012): opaque `token_urlsafe(48)` values,
  stored only as SHA-256 hashes, rotated on every use, grouped by
  `family_id`. Presenting an already-rotated token is treated as theft: the
  **entire family is revoked** and the client must re-authenticate. Logout
  revokes the family.
- **API keys** (migration 013): `kf_` + 32 bytes of entropy, SHA-256 hashed
  at rest with a short clear prefix kept for identification; plaintext is
  returned exactly once at creation; `get_current_user` accepts
  `X-API-Key` after the bearer path fails; `last_used_at` is recorded.

**Why this design:** rotation-with-family-revocation is the only replay
detection possible with opaque tokens — a stolen token is indistinguishable
from its owner until someone uses the old one ([decisions: refresh tokens]).
Keys are prefixed (`kf_`) so secrets scanners can pattern-match them, hashed
so a database leak doesn't leak credentials, and shown once because a
re-displayable secret can't be treated as compromised-on-sight. Refresh
tokens live in PostgreSQL, not Redis — avoiding a new dependency was worth
the (small) write cost. API-key traffic flows into the same
`request_logs`/usage pipeline, so cost attribution doesn't need a second
metering path.

**Future hooks (F4):** email verification/password reset (needs a mail
sender — deliberate decision pending); scoped keys (per-key permissions) are
a natural extension of the `api_keys` table.

---

## 5. Retrieval and search

**What:** cosine-similarity vector search over tenant chunks, with optional
per-document and per-type scoping and an off-by-default hybrid lexical path.

**How it works:** the question is embedded and `retrieve_chunks` orders by
`embedding <=> query` (pgvector cosine distance) with mandatory tenant
filters plus optional `doc_type` and `document_id` filters. The hybrid path
(migration 009's `tsvector` column) adds a weighted
`ts_rank` term for exact-match questions; it is exposed via
`hybrid_search_enabled` + `hybrid_lexical_weight` but **defaults to off**.

**Why this design:** hybrid scored *below* pure vector on the local
diagnostic run (30%/35% vs 35%/40% Hit@5), and the project's core rule is
that retrieval changes follow measured evidence, not intuition
([decisions: hybrid off by default]). The final adoption call waits on the
Gemini-keyed evaluation (R3.3/R3.5); if adopted, the eval gate protects it.
Chunking (500/100) is likewise a *default*, not a constant — profiles are
config-driven so experiments run without code changes.

**Future hooks (F2/F7):** reranking only if R3.5 evidence justifies it
(never both speculatively); HNSW index swap is deferred until real P95
numbers exist because index choice is a recall/latency trade-off that
depends on corpus size and query mix, neither of which is known yet
([decisions: F7 HNSW deferred]).

---

## 6. Conversations and streaming answers

**What:** multi-turn conversations with persisted history and citations,
follow-up questions that resolve pronouns from context, and SSE token
streaming for perceived latency.

**How it works:** `/ask` and `/ask/stream` share one `_prepare_ask` core —
load history, rewrite the follow-up into a standalone question (best-effort:
provider failure degrades to the raw question), embed, retrieve, label
chunks into an `AskContext`. The non-streaming path generates and returns
JSON; the streaming path emits SSE `token` events as Gemini produces them
and a final `done` event with parsed citations. The persisted question is
the user's *original* wording; retrieval uses the *rewritten* one. History
is treated as quoted data in the rewrite prompt, mirroring the main prompt's
injection defense.

**Why this design:** one core means retrieval logic cannot drift between
the two endpoints ([decisions: shared ask core]) — before the refactor the
streaming path would have duplicated auth/history/retrieval with subtle
differences. Everything before the first token surfaces as a normal HTTP
error (not an SSE error event), so clients can retry without parsing the
stream. The rewrite is deliberately before retrieval: an embedded "and what
about page 4?" retrieves garbage; a standalone rewrite retrieves correctly,
and a failed rewrite is survivable because self-contained questions still
work raw.

**Future hooks (F1):** conversation titles/summarization, retrieval-chunk
reuse across turns; the `messages` table already stores citations per
exchange, so a "show me what it cited then" UI needs no schema change.

---

## 7. Embedding cache

**What:** identical chunk text is never re-embedded — re-uploads, re-ingests,
and cross-tenant duplicate content cost zero embedding tokens.

**How it works:** `embed_texts_cached` keys the `embedding_cache` table
(migration 011) by `content_hash(f"{model}:{text}")`, serves hits from the
table, embeds only the misses, and stores the batch-average token count
per chunk. Both the worker pipeline and the synchronous paths use it.

**Why this design:** the model is part of the key, so switching
`GEMINI_EMBEDDING_MODEL` can never serve stale vectors from the previous
model — a silent wrong-answer bug class eliminated structurally
([decisions: embedding cache]). Token telemetry counts only the miss batch,
so the usage dashboard reflects *actual* API spend, which keeps the cost
numbers trustworthy enough to bill against later. Per-chunk token counts
aren't reported by the batch API; storing the batch average is honest
bookkeeping rather than invented precision.

**Future hooks (F5):** per-tenant budget alerts can key off
`embedding_cache.input_tokens` + `request_logs` — both already carry real
numbers (post R4.5).

---

## 8. Document management

**What:** list/detail with pagination, chunk preview ("what did we index?"),
re-ingestion, duplicate detection, document deletion (GCS included), and
full account deletion.

**How it works:** `GET /documents` (paginated, tenant-scoped) and
`GET /documents/{id}` detail; `GET /documents/{id}/chunks` returns the
indexed chunks so users can verify extraction quality before trusting
answers; `POST /documents/{id}/reingest` resets status (only
`ready`/`failed` documents — anything else is a 409) and republishes the
job; delete returns 204 and removes the GCS object, distinguishing "row
absent" (404) from "no storage to clean" (204).

**Why this design:** chunk preview is the cheapest trust-building feature
in a RAG product — users who can see what was indexed can self-diagnose
extraction problems instead of filing "the answer was wrong" bugs. The
re-ingest 409 exists because re-ingesting a document that is *currently
being processed* would race the claim lease. The delete distinction (404 vs
204, review C2) replaced a bug where sync-ingested documents 404'd on
delete because they had no `storage_uri`.

**Future hooks (F3):** versions/supersession chains build on the content
hash (a new hash for the same filename is a new version — the key structure
`{tenant}/{hash}/{filename}` already supports it); retention policies are
an F8 legal-review item first.

---

## 9. Rate limiting

**What:** per-subject token buckets — Redis-backed when configured,
per-process otherwise, degrading rather than failing on Redis loss.

**How it works:** one atomic Lua script per request does read-refill-check-
write in a single Redis round trip, keyed `knowledgeforge:limit:{subject}:{key}`,
using **epoch time** (not monotonic). On any Redis error the limiter logs a
warning and delegates to an in-process `TokenBucketLimiter` for that call.
The in-process bucket map is capped at 10,000 entries and evicts
fully-refilled (idle) buckets first.

**Why this design:** the Lua script means no read-modify-write race between
replicas. Epoch time is load-bearing: monotonic clocks are per-process, so
cross-replica refill arithmetic computed garbage deltas (review C4) — this
was a real multi-replica bug, not theoretical. The fallback exists because
a rate limiter that 500s every request when Redis blips is worse than no
limiter ([decisions: Redis limiter]); the chaos drill *proves* it — the
Redis-blackhole drill asserts auth, upload, and ask all succeed with Redis
unreachable. `/health` deliberately checks nothing so the API stays
"healthy" during a Redis outage and Kubernetes doesn't make things worse by
restarting it.

**Future hooks:** per-tenant (vs per-user) quota tiers are a config change
in the key structure; F5 budget alerts complement rather than replace this.

---

## 10. Reliability layer

**What:** explicit timeouts, bounded retries, and a circuit breaker around
every external call — plus fail-closed startup validation.

**How it works:** a process-wide `CircuitBreaker` wraps the Gemini paths
(API and worker); open state returns a fast 503 "Model provider temporarily
unavailable" instead of hanging callers. `with_retry` (tenacity, one retry,
exponential backoff) wraps generation, batched embedding, GCS operations,
and Pub/Sub publish. Every provider client is constructed with an explicit
timeout (`http_options`, default 30s). At boot, `validate_runtime()` refuses
to start outside development with a default/short JWT secret or a missing
Gemini key (unless both `LOCAL_EMBEDDINGS` and `LOCAL_GENERATION` are set).

**Why this design:** the architecture review found a "reliability layer"
that existed but wasn't wired to anything — claims without behavior
(findings H1/H5). The breaker is process-wide because one wedged provider
connection shouldn't be retried by every request in parallel. Retrying
generation is justified because an answer request is idempotent from the
caller's perspective ([decisions: retry under the breaker]) — if metered
generation is added, revisit. Fail-closed boot closes the "forgot the env
var, running on public defaults" hole: a misconfigured service never serves
a request, it dies loudly at deploy time.

**Future hooks:** the breaker currently covers Gemini only; GCS/Pub/Sub
rely on client-default retries — if load tests show cascade failures there,
the same breaker pattern extends.

---

## 11. Observability and cost telemetry

**What:** per-request logs with real token counts and cost estimates, a
tenant usage dashboard, structured worker logs with durations.

**How it works:** Gemini usage metadata (input/output tokens) is captured
on both embed and generate paths and written to `request_logs` with a
computed `cost_estimate` and the actual retrieved chunk IDs; `GET
/admin/usage` aggregates documents, queries, and a daily token series per
tenant. The worker logs `job.start`/`job.success`/`job.failure` with
durations. Logging honors `settings.log_level` and is structured end to
end.

**Why this design:** before R4.5 the system claimed cost tracking it didn't
do (review H3) — the usage dashboard would have shown zeros. Token counts
are taken from provider metadata, never estimated, and cached embeddings
count only misses, so the numbers are honest enough to bill against.
`retrieved_chunk_ids` stores real chunk IDs (joined from retrieval), which
is what makes per-miss eval diagnostics ("retrieval miss vs generation
miss") possible — the same column powers debugging and evaluation.

**Future hooks (F5):** budget alerts (threshold on the daily token series),
role-gated admin console for dead-letter inspection and failed-ingestion
triage.

---

## 12. Local execution modes and the emulator stack

**What:** the entire system — including cited answers — runs locally with
zero cloud credentials.

**How it works:** `docker-compose.full.yml` stands up PostgreSQL+pgvector,
Redis, fake-gcs-server, and the Pub/Sub emulator; an init job creates the
topic, subscription, and dead-letter policy. `LOCAL_EMBEDDINGS` swaps Gemini
embeddings for deterministic hash-based vectors; `LOCAL_GENERATION` swaps
generation for a deterministic local answer that cites every retrieved chunk
in the standard `[doc N, page M]` format. A smoke test exercises the full
lifecycle: register → upload → duplicate-upload → worker → `ready` → ask
(plain + streaming) → delete → 404 → dead-letter delivery → account deletion
→ GCS-object-gone assertion.

**Why this design:** the R6 evidence requirement ("every defined-but-never-
executed item produces recorded output") is unreachable if the core loop
needs a Gemini key. `LOCAL_GENERATION` reuses the *entire* pipeline —
retrieval, citation parsing, telemetry, conversation persistence, SSE —
only the provider call is replaced, so the smoke test proves the whole
loop, not a stub of it ([decisions: LOCAL_GENERATION]). Startup validation
still demands a real key unless both flags are set, so these modes can't
leak into production by accident.

**Future hooks:** GCS-500 fault injection (F6) would be a third compose
override following the chaos-override pattern.

---

## 13. Quality system (the project's spine)

**What:** four interlocking gates — eval thresholds, coverage floor, chaos
drills, and the evidence runner — that make "no regression" a mechanical
check.

**How it works:**
- **Eval ratchet**: `evaluation/check_thresholds.py` scores the 40-question
  golden set (exact-match IDs, error codes, unanswerable refusals included)
  against `eval-thresholds.json`; floors only rise, via explicit `--update`.
  Runs on release branches/dispatch (cost control), not every PR.
- **Coverage ratchet**: `scripts/coverage_ratchet.py` against
  `.coverage-floor`, same ratchet discipline, runs on every unit-test CI
  job.
- **Chaos drills**: a compose *override* (`docker-compose.chaos.yml`) plus
  `scripts/emulator_chaos_test.py` — the Redis-loss drill points the API's
  `REDIS_URL` at a listener-less container and asserts requests still
  succeed; the redelivery storm publishes 10 malformed messages and asserts
  all 10 reach the DLQ, then that a valid upload still processes. Wired as
  a CI job.
- **Evidence runner**: `scripts/r6_evidence.{sh,ps1}` chains quality gates →
  unit → integration → emulator smoke → load → chaos → backup/restore,
  tees every step's output into `docs/evidence/<timestamp>/`, and records
  PASS/FAIL per step.

**Why this design:** floors-as-ratchets convert quality from a review-time
promise into CI arithmetic — the floor can only move down with an explicit
justification in the PR ([decisions: quality ratchets]). Chaos is a compose
override rather than pytest because the drills assert whole-system timing
against a deliberately broken stack over tens of seconds — retry exhaustion
needs the real emulator's redelivery timing, which process-model tests
can't express ([decisions: chaos drills]). Locust (`scripts/locustfile.py`)
profiles real traffic shapes (8:1 ask:upload) because an artificial
all-asks load test measures a system that doesn't exist.

**Future hooks (F6):** LLM-as-judge scoring after the deterministic metric
proves insufficient; GCS fault injection as above.

---

## 14. Infrastructure and deployment

**What:** two deployment paths with one contract — Terraform as the source
of truth for production, free-tier deploy scripts as the fast path.

**How it works:**
- **Terraform** (`infrastructure/terraform/`): Cloud Run API + worker
  (dedicated service accounts, not default-compute), Secret Manager mounts,
  Cloud SQL private-IP with PITR + deletion protection, Pub/Sub push
  subscription with OIDC and a dead-letter topic, monitoring/SLOs as code.
  CI runs `fmt -check` + `validate` always; a credential-gated workflow
  posts `plan` for review. **Apply is always manual.**
- **Deploy scripts** (`scripts/deploy-cloudrun.{sh,ps1}`,
  `pull-and-deploy.{sh,ps1}`): gcloud-native one-command path following the
  the-visualizer pattern — pull/rebase, run the same checks CI runs, build
  both images in one Cloud Build submission, wire secrets/Pub/Sub/IAM,
  migrations, health smoke. Idempotent. `DATABASE_URL` comes from the
  environment (free-tier Postgres host) instead of provisioning Cloud SQL.

**Why this design:** Terraform remains the reviewed source of truth, but
requiring a full Terraform graph just to demo the system is a barrier the
scripts remove — and any drift between the two is treated as a bug in one
of them, keeping the fast path honest. Apply is manual by design:
infrastructure changes are reviewed by a human, not merged and executed
([decisions: Terraform change process]). Push delivery with a dedicated
service account plus in-app OIDC audience verification gives defense in
depth — Cloud Run invoker IAM plus the app rejecting tokens from the wrong
principal.

**Future hooks (R7/Phase 15):** both paths execute against a real GCP
project next (gated on credentials); the launch checklist sequences the
staging validation.

---

## 15. Database and migration discipline

**What:** 13 forward-only migrations (001–013) as the only schema-change
path.

**How it works:** migrations are plain SQL applied in order by
`scripts/apply_migrations.py`; fresh-database application is tested in CI.
Rollback is backup-restore plus reviewed forward-repair SQL — there are no
down-migrations.

**Why this design:** the migrations alter existing tables, data, and
indexes; an automatic down-migration would create a false sense of safety
([decisions: migration rollback policy]). Every new table this cycle
(claim lease 008, hybrid tsvector 009, conversations 010, embedding cache
011, refresh tokens 012, API keys 013) shipped *with* the code that uses it
and tests against it — schema and behavior land together.

**Future hooks:** F4's per-tenant email uniqueness and any F3 versions
table follow the same rule: forward migration + code + tests in one PR.

---

## 16. Local development and CI tiers

**What:** a tier ladder where every tier runs what it can prove without the
credentials of the tier above it.

| Tier | Runs | Needs |
|---|---|---|
| Unit | ruff, ruff-format, mypy strict, pytest + coverage ratchet | nothing |
| Integration | tenant isolation, auth extensions, conversations, local ask against real PostgreSQL + Redis | containers |
| Emulator stack | full-lifecycle smoke test (CI job) | Docker only |
| Chaos | Redis-loss + redelivery drills (CI job) | Docker only |
| Live | Gemini smoke, prompt-injection behavior, phase-12 eval, Terraform plan | credentials |

**Why this design:** the account-agnostic strategy (from the original plan)
means the project's honesty doesn't depend on having a cloud account —
everything provable locally is proven locally, and credential-gated tiers
are *wired and waiting*, not stubbed. `pythonpath = ["."]` in pytest config
lets tests import the `evaluation/` and `scripts/` tooling directly, so the
ratchets are tested like any other code.

**Future hooks:** pip-audit/CodeQL/Trivy run in hosted CI (R6 item);
scheduled Gemini eval completes R3.3's evidence loop.

---

## Planning map — feature → roadmap

Use this table to start future-phase planning from the current design, not
from scratch.

| Feature (this doc) | Built | Natural next step (roadmap ref) |
|---|---|---|
| Ingestion pipeline | ✅ | pptx/CSV extraction, versions/supersession, retention (F3) |
| Grounded citations | ✅ | LLM-as-judge correctness scoring (F6.2) |
| Multi-tenancy | ✅ | Multi-user tenants: roles, invitations (F4.2) |
| Auth | ✅ | Email verification / password reset — needs mail sender decision (F4.4); scoped keys |
| Retrieval | ✅ | Hybrid adoption call after real eval (R3.5); reranking only if justified (F2.1) |
| Conversations/streaming | ✅ | Titles/summarization; chunk-reuse across turns (F1 extensions) |
| Embedding cache | ✅ | Budget alerts keyed off real token numbers (F5.2) |
| Document management | ✅ | Versions UI, retention policies (F3.1/F3.4) |
| Rate limiting | ✅ | Per-tenant quota tiers (config-level) |
| Reliability layer | ✅ | Extend breaker to GCS/Pub/Sub if load tests demand (R2 extension) |
| Observability | ✅ | Role-gated admin console, budget alerts (F5.2/F5.3) |
| Local modes | ✅ | GCS-500 fault injection as a third compose override (F6.3) |
| Quality system | ✅ | Raise floors from real numbers; LLM judge (F6.2) |
| Infra/deployment | ✅ | Execute against real GCP (R7/Phase 15); keep scripts↔Terraform in sync |
| Database | ✅ | All future schema via forward migrations (per-tenant email, versions) |
| CI tiers | ✅ | Security scanning in hosted CI (R6.7); scheduled eval artifact (R6.9) |
| Structured extraction (17) | ✅ | Schema #2 after invoice accuracy numbers; review UI; verifier call if miscalibrated |

**Deliberately not built (and why — keep it that way until the trigger):**
HNSW indexing (until real P95 numbers, F7), read replicas (until write
contention is measured, F7), billing (until external paying users, F5.4),
SOC2 claims (schedule honestly or don't claim, F8.4).

---

*This document describes design intent as of 2026-09-04. Behavioral truth
lives in the tests and in [`validation-status.md`](validation-status.md);
if this file and the evidence disagree, the evidence wins — fix this file.*
