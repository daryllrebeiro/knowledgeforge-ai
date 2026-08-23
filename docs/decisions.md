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

Local deterministic diagnostic run on 2026-08-23:

| Profile | Vector Hit@5 | Hybrid Hit@5 | Local decision |
|---|---:|---:|---|
| baseline 500/100 | 35% | 30% | do not adopt hybrid locally |
| large 800/150 | 40% | 35% | do not adopt hybrid locally |

These numbers are not the final Phase 12 result because they use deterministic local
embeddings. The production decision remains pending the scheduled real-Gemini run.
