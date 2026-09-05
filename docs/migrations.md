# Migration policy

Migrations `001` through `013` are forward-only at this stage. They are applied in
numeric order by `scripts/apply_migrations.py` against a fresh database in CI.

`008_worker_claim_lease.sql` adds `documents.status_changed_at` to support the
worker's atomic claim lease (`store.claim_document`): `pending` → `processing`
with a 10-minute lease so Pub/Sub at-least-once redeliveries cannot
double-process a document, and a crashed worker's claim expires and can be
re-claimed.

`009_hybrid_search.sql` adds a `tsvector` column generated from `chunk_text`
plus a GIN index, enabling optional hybrid (vector + lexical) retrieval behind
`HYBRID_SEARCH_ENABLED`.

`010_conversations.sql` adds the `conversations` and `messages` tables for
multi-turn asking (F1). Both cascade from `tenants`, so account deletion
removes conversation history; messages cascade from their conversation.

`011_embedding_cache.sql` adds the `embedding_cache` table (content hash primary
key, 768-dimension vector, token usage) backing
`ingestion.embed_cache.embed_texts_cached` — identical chunk text skips the
embedding call entirely.

`012_refresh_tokens.sql` adds the `refresh_tokens` table: SHA-256 token hashes
(unique), `family_id` for rotation-with-replay-detection, and revocation
timestamps. Tokens cascade from users and are indexed per user and per family.

`013_api_keys.sql` adds the `api_keys` table: SHA-256 key hashes (unique, with a
partial index excluding revoked keys), a clear `key_prefix` for identification,
and `last_used_at`. Keys cascade from tenant and user.

`014_structured_extraction.sql` adds the Phase 2.5 tables:
`document_extractions` (validated fields as JSONB keyed uniquely by
`(tenant_id, content_hash, schema_type, schema_version, model)` — tenant-scoped
like the documents hash constraint, so identical bytes in two tenants are two
extractions), `failed_extractions` (mirrors `failed_ingestions`), and
`extraction_jobs` with a partial unique index allowing at most one
queued/processing job per document (the reprocess 409). The `extraction_outbox`
table backs the transactional outbox: the ingestion transaction writes the
ready state and an unsent event together; a bounded dispatcher publishes and
marks sent. `documents` gains `detected_doc_type`/`doc_type_confidence` so
classification is queryable even for documents that never get an extraction
row. All new tables cascade from tenants and documents.

The current migration set does not include destructive down-migrations because several
steps transform existing data or indexes. Recovery is therefore handled by restoring a
database backup and applying forward repair SQL under review. Before production use,
each migration that needs rollback support must either receive a tested down-migration
or have a specific forward-repair procedure recorded here.

Phase 10 CI proves fresh-database application. Rollback/recovery drills are scheduled
for Phase 13.
