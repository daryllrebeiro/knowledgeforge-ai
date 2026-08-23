# KnowledgeForge operations runbook

## Error-rate spike

Check Cloud Run structured logs by `request_id` and route. Confirm whether failures are
authentication, database, Gemini, or worker errors. If Gemini failures dominate, inspect
the circuit-breaker state and provider status before increasing capacity.

## P95 latency breach

Check `/ask` request logs for retrieval and generation latency, then inspect database
connections and pgvector query plans. Compare the affected tenant and route; do not
increase worker/API concurrency until Gemini rate limits are ruled out.

## Dead-letter queue non-empty

Inspect the Pub/Sub message and `documents.status`. Reproduce the failing document with
the worker locally, fix the parser or provider issue, then replay only after the cause is
understood. A redelivery must remain idempotent.

## SLO targets

- `/ask` availability: 99% excluding client errors
- `/ask` P95 latency: under 5 seconds
- 20-page ingestion P95 time-to-ready: under 2 minutes

## Rate limiting tradeoff

Phase 7 uses an in-memory token bucket keyed by tenant and route. This is correct for a
single API instance and keeps the first deployment simple. It must be replaced with a
shared Redis or gateway limiter before scaling the API horizontally; otherwise each
instance would have an independent quota window.
