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

## 2026-08-23 — Application-level JWT tenant identity

We chose signed JWTs containing both user and tenant IDs, validated by a FastAPI
dependency. Storage and retrieval functions receive the tenant ID explicitly, so tenant
scoping is enforced in SQL rather than by filtering results after retrieval. This keeps
the first-party auth surface small while preserving a clear migration path to an
external identity provider later.
