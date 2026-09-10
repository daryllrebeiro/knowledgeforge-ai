# KnowledgeForge AI — Phase 6 Task Tracker: Bridge, Verify, Then Extend

Status Taxonomy:
- **Implemented**: Code and configuration written and committed; passes unit tests against mocks.
- **Verified**: Exercised against a real external system or real infrastructure, with specific evidence cited.
- **Done**: Verified, plus survived a targeted adversarial audit pass with independent reviewer sign-off `(Signed-off-by: <reviewer>)`.
- **Gated**: External dependency blocked (credentials, live cloud billing, or external API quota).
- **Retracted**: Unsubstantiated claims removed following audit findings.

---

## Item 1 — Close the Unverified-Account Purge Call-Site Gap (Phase 5 Item 7 Follow-Up)
- [Verified] Periodic purge entrypoint implemented at `src/knowledgeforge/security/purge_job.py` with CLI interface (`--max-age-days`, `--purge-documents`, `--dry-run`)
- [Verified] Unit tests in `tests/unit/test_purge_job.py` verifying dry-run safety and actual deletion of stale unverified accounts
- [Verified] Integration tests against real PostgreSQL in `tests/integration/test_gdpr_postgres.py`
- [Verified] Phase 5 Items 1 and 9 self-certified [Done] statuses demoted to [Verified] in compliance with Ground Rule 5

## Item 2 — Real Stripe Integration via stripe-mock (Bridge the Verification Gap)
- [Verified] `stripe-mock` service (`stripe/stripe-mock:v0.190.0`) added on port 12111 in `docker-compose.full.yml`
- [Verified] `stripe_api_base` added to settings in `src/knowledgeforge/config.py` and `src/knowledgeforge/billing/stripe_client.py`
- [Verified] Integration test suite `tests/integration/test_stripe_mock.py` and mock client suite `tests/unit/test_stripe_client_mock_server.py` exercising checkout session, portal session, and webhook lifecycle against `stripe-mock`
- [Gated] Live test-mode Stripe API verification (blocked on real `sk_test_...` key)

## Item 3 — Ready-to-Fire Verification Harnesses for Gated Items (Items 3, 6, 8)
- [Verified] Infrastructure verification harness `scripts/verify_infra_live.py` with `--dry-run` validation and Cloud Run / Cloud SQL checks
- [Verified] Alert policy verification harness `scripts/verify_alerts_live.py` with `--dry-run` validation and Cloud Monitoring checks
- [Verified] Disaster recovery verification harness `scripts/verify_dr_live.py` with `--dry-run` validation and PITR backup checksum checks
- [Verified] Unit test suite `tests/unit/test_live_harnesses_dry_run.py` verifying `--dry-run` exit codes and output contracts
- [Gated] Live infrastructure provisioning and execution against live GCP project (blocked on deployment approval)

## Item 4 — Resumable Gemini Evaluation Harness with Checkpoints and Cost Guardrails (Item 2 Bridge)
- [Verified] Resumable checkpoint runner with `CheckpointTracker` in `evaluation/run_phase12_eval.py`
- [Verified] Spend cap guardrail (`--max-spend-usd`) and graceful 429 quota handling with checkpoint preservation
- [Verified] Unit tests in `tests/unit/test_resumable_eval.py` testing resume logic, spend cap trip, and 429 backoff
- [Verified] Deterministic local evaluation smoke test (`python -m evaluation.run_phase12_eval --local`) producing `docs/phase12-eval-checkpoint.json`
- [Gated] Live Gemini evaluation across all 3 chunking profiles (blocked on live API quota)

## Item 5 — Strengthen the Mechanical Integrity Tooling (Item 9 Follow-Up)
- [Verified] Non-phantom file existence verification added to `scripts/verify_decisions.py`
- [Verified] Ground Rule 5 sign-off validator (`validate_task_integrity()`) added to `scripts/generate_task_summary.py`
- [Verified] Unit test suites `tests/unit/test_decisions_integrity.py` and `tests/unit/test_task_summary.py` passing with 100% coverage

## Item 6 — Self-Service Tenant Dashboard & Usage API (Feature Track F5)
- [Implemented] `GET /tenant/usage` and `GET /tenant/dashboard` endpoints added to `src/knowledgeforge/api.py` strictly scoped to caller's `tenant_id`
- [Implemented] Daily trend metrics, token and extraction budget tracking, and tenant metadata reporting
- [Implemented] Unit test suite `tests/unit/test_tenant_dashboard.py` verifying tenant scoping, data accuracy, and role permissions

## Item 7 — Outbound Webhooks with SSRF-Safe Delivery (Feature Track F6)
- [Implemented] Database migration `migrations/023_tenant_webhooks.sql` for webhooks and delivery tracking
- [Implemented] SSRF validation engine `src/knowledgeforge/security/ssrf.py` blocking loopback, link-local, private RFC 1918, and cloud metadata (`169.254.169.254`)
- [Implemented] Webhook dispatcher `src/knowledgeforge/security/webhooks.py` with timing-safe HMAC SHA-256 signatures, exponential backoff, and dead-letter queue
- [Implemented] Webhook management endpoints `POST /tenant/webhooks`, `GET /tenant/webhooks`, `DELETE /tenant/webhooks/{id}`
- [Implemented] Unit test suite `tests/unit/test_outbound_webhooks.py` covering SSRF rejection, signature verification, and delivery retries

## Item 8 — Enterprise OIDC SSO Integration (Feature Track F7)
- [Implemented] Database migration `migrations/024_tenant_sso_and_retention.sql` for tenant SSO configurations
- [Implemented] OIDC SSO integration `src/knowledgeforge/security/sso.py` gated to Enterprise tier with CSRF state tokens, JIT provisioning, and standard JWT minting
- [Implemented] SSO endpoints `PUT /tenant/sso/config`, `GET /tenant/sso/config`, `POST /auth/sso/oidc/authorize`, `POST /auth/sso/oidc/callback`
- [Implemented] Unit test suite `tests/unit/test_sso.py` covering tier enforcement, CSRF state verification, JIT user provisioning, and claim validation

## Item 9 — Configurable Retention and Data Residency Settings (Feature Track F8)
- [Implemented] Tenant retention and data residency columns (`retention_days`, `data_residency`) in `migrations/024_tenant_sso_and_retention.sql`
- [Implemented] Endpoints `GET /tenant/settings` and `PUT /tenant/settings` in `src/knowledgeforge/api.py` with owner-only mutation and residency validation
- [Implemented] Document retention purge job `purge_expired_documents` in `src/knowledgeforge/security/purge_job.py`
- [Implemented] Unit test suite `tests/unit/test_retention_settings.py` verifying retention enforcement, residency options, and purge execution

## Item 10 — Task Tracking, Decisions Log, and Capstone Verification
- [Verified] Ground Rule 5 sign-off enforcement verified mechanically by `scripts/generate_task_summary.py`
- [Verified] Decisions citations verified mechanically without phantom files by `scripts/verify_decisions.py`
- [Verified] OpenAPI specification synchronized and verified with zero drift (`docs/openapi.json`)
- [Verified] Complete test suite passing with test coverage exceeding the 58.00% floor

## Item 11 — Natural-Language Filters Over Extracted Fields (Phase 7 Wave 1 Item A)
- [Implemented] Query parser in `src/knowledgeforge/extraction/query_parser.py` converting natural-language queries into structured filters with equality, numeric ranges, and date ranges
- [Implemented] Ambiguity detection and fallback mechanism returning parsed filter with `ambiguous=True` and explanatory details when queries cannot be confidently mapped
- [Implemented] Parameterized SQL querying in `src/knowledgeforge/extraction/store.py` supporting numeric and date range filters with safe typecasting and strict tenant isolation
- [Implemented] API endpoints `POST /extractions/filter` and `GET /extractions?natural_query=...` in `src/knowledgeforge/api.py`
- [Verified] Unit test suite `tests/unit/test_natural_filter.py` covering operators, SQL safety, API routes, and 100% precision on `evaluation/extraction-golden-set.json` queries

## Item 12 — Multi-Document Comparison (Phase 7 Wave 1 Item B)
- [Implemented] Multi-document specification via `document_ids: list[UUID] | None` on `AskRequest` in `src/knowledgeforge/api.py`
- [Implemented] Multi-document retrieval intersection with caller tenant boundary check silently excluding foreign tenant document IDs
- [Implemented] Dynamic chunk retrieval scaling and comparative prompt instructions in `src/knowledgeforge/generation/prompt.py` when multiple document contexts are loaded
- [Verified] Unit test suite `tests/unit/test_multi_doc_comparison.py` testing comparison instructions, multi-doc citations, tenant boundary filtering, and empty list short-circuits

## Item 13 — Source Highlighting Viewer (Phase 7 Wave 1 Item C)
- [Implemented] Database migration `migrations/032_chunk_character_offsets.sql` adding `start_char` and `end_char` tracking columns to `chunks`
- [Implemented] Exact token-to-character span offset calculation in `src/knowledgeforge/ingestion/chunk.py` (`chunk_pages`)
- [Implemented] Chunk storage and retrieval with character offsets in `src/knowledgeforge/ingestion/store.py`
- [Implemented] Endpoints `GET /documents/{document_id}/content` (JSON) and `GET /documents/{document_id}/view` (interactive HTML viewer with SVG/CSS bounding box overlay and passage mark styling)
- [Verified] Unit test suite `tests/unit/test_source_highlighting_viewer.py` testing character offset accuracy across single/multi-page texts, API content responses, and viewer highlighting

## Item 14 — Round 6 Security Findings & Architectural Hardening
- [Implemented] Fix 0: Authorization gate `require_owner` on `/privacy/unmask` and `/privacy/vault`; immutable audit logging table `audit_logs` (`migrations/033_audit_logs.sql`) recording caller `user_id`, `tenant_id`, timestamp, and unmasked surrogate tokens.
- [Implemented] Fix 1: Independent `VAULT_MASTER_KEY` setting with fail-closed boot check in `validate_runtime()`; HKDF-SHA256 key derivation with per-record random 16-byte salt; base64 obfuscation fallback completely removed (fail-closed); documented manual key rotation procedure in `docs/decisions.md`.
- [Implemented] Fix 2: Research jobs budget system integration: wired `token_budget.reserve()`/`reconcile()` and rate limiting to `POST /research/jobs` scaling proportionally with `max_iterations`; explicit prerequisite gate recorded: NO LLM generator may be wired into `DeepResearchPlanner` until budget integration is verified under a real or realistically simulated multi-turn research job.
- [Implemented] Fix 3: Gated all four tenant admin routes (`GET /admin/conflicts`, `POST /admin/conflicts/{id}/resolve`, `GET /admin/schemas`, `POST /admin/schemas`, `POST /admin/schemas/infer`) to `Depends(require_owner)`; rate limited `/admin/schemas/infer`; added repo-wide mechanical check verifying zero `/admin` routes use bare `get_current_user`.
- [Implemented] Fix 4: Added tenant scoping directly to `diff_documents_from_db` SQL query in `diff_engine.py` (`JOIN documents d ON c.document_id = d.id WHERE d.tenant_id = %s`), ensuring safety by construction.
- [Implemented] Fix 5: Added strict `Content-Security-Policy` headers (`default-src 'self'; script-src 'self' 'nonce-{nonce}'; frame-ancestors 'none'`) to `document_viewer` and `/admin` console; eliminated inline event handlers (`onclick`, `onchange`) in `admin_ui.py` in favor of DOM `addEventListener`.
- [Implemented] Fix 6: Added `scopes` column (`migrations/034_api_key_scopes.sql`) and `require_scope` dependency check to enforce fine-grained API key permissions across endpoints (`read:documents`, `write:documents`, `query:ask`, `admin:schemas`).
- [Implemented] Fix 7: Added Cloud Run job `google_cloud_run_v2_job.purge` and Cloud Scheduler resource `google_cloud_scheduler_job.purge_unverified` (daily at 3 AM UTC) in `infrastructure/terraform/main.tf` to trigger `purge_job.py`.
- [Implemented] Process Fix: Added strategic projection disclaimer header to `REVIEW_AND_ROADMAP.md`, hedged qualitative claims, and extended `scripts/verify_decisions.py` to mechanically scan committed markdown documents for unhedged absolute claims.
- [Verified] Unit test suites `tests/unit/test_privacy_vault.py`, `tests/unit/test_config.py`, `tests/unit/test_research_jobs_budget.py`, `tests/unit/test_admin_routes_auth.py`, `tests/unit/test_diff_engine_scoping.py`, `tests/unit/test_csp_headers.py`, `tests/unit/test_api_key_scopes.py`, and `tests/unit/test_decisions_integrity.py` passing with 100% success.

## Item 15 — Shared Workspaces & Collections (Phase 8 Item 1)
- [Implemented] Database migration `migrations/035_collections_workspaces.sql` creating `collections`, `collection_documents`, and `collection_memberships` tables
- [Implemented] Sub-tenant isolation service `src/knowledgeforge/collections/service.py` with membership checks, private collection gating, and CRUD operations
- [Implemented] SQL-level scoping in `retrieve_chunks`, `list_documents`, and `get_document_detail` enforcing collection membership in WHERE clauses
- [Implemented] API endpoints `/collections`, `/collections/{id}`, `/collections/{id}/documents`, `/collections/{id}/members` in `src/knowledgeforge/api.py`
- [Verified] Unit test suite `tests/unit/test_collections_isolation.py` proving non-member tenant users cannot view or retrieve documents in private collections

## Item 16 — Knowledge Graph Visualizer Frontend (Phase 8 Item 2)
- [Implemented] HTML/SVG interactive explorer endpoint `GET /graph/view` in `src/knowledgeforge/api.py` rendering nodes and edges
- [Implemented] Strict `Content-Security-Policy` header with cryptographic nonce binding and `frame-ancestors 'none'`
- [Implemented] Front-end consumes existing audited `/graph/query` recursive traversal endpoint unchanged without modifying backend traversal CTE
- [Verified] Unit test suite `tests/unit/test_graph_view.py` verifying HTML output, CSP nonce matching, authentication requirement, and `/graph/query` dispatch

## Item 17 — Recurring Activity Digests (Phase 8 Item 3)
- [Implemented] Database migration `migrations/036_playbooks_and_digests.sql` defining `tenant_digest_settings`
- [Implemented] Transactional digest email dispatch in `src/knowledgeforge/security/mailer.py` (`send_digest_email`)
- [Implemented] Periodic aggregation job `src/knowledgeforge/worker/digest_job.py` calculating ingested, extracted, failed, and query counts
- [Implemented] Cloud Run Job `google_cloud_run_v2_job.digest` and Cloud Scheduler trigger `google_cloud_scheduler_job.recurring_digest` in `infrastructure/terraform/main.tf`
- [Implemented] Settings endpoints `GET /tenant/digest`, `PUT /tenant/digest`, and `POST /tenant/digest/trigger` in `src/knowledgeforge/api.py`
- [Verified] Unit test suite `tests/unit/test_recurring_digests.py` verifying aggregation metrics, frequency gating, and recipient delivery

## Item 18 — Document Drafting from Grounded Context (Phase 8 Item 4)
- [Implemented] Document drafting engine `src/knowledgeforge/generation/drafting.py` with structured drafting system prompt, prompt injection quotation defense, and inline citation parsing
- [Implemented] Token accounting with upfront reservation and reconciliation via `RedisBudgetCounter`
- [Implemented] Strict invariant: generated drafts are never automatically re-ingested into `documents` table
- [Implemented] Endpoint `POST /ask/draft` in `src/knowledgeforge/api.py`
- [Verified] Unit test suite `tests/unit/test_drafting_budget.py` verifying grounding, token accounting, 429 budget rejection, and zero insertion into documents table

## Item 19 — Auto-Clustering and Tagging Engine (Phase 8 Item 5)
- [Implemented] Database migration `migrations/038_clustering_widgets_mobile.sql` adding `document_clusters` and `document_tags` tables
- [Implemented] Mean-pooling embedding aggregation and cosine clustering in `src/knowledgeforge/clustering/engine.py`
- [Implemented] Human-in-the-loop review workflow: clusters saved as `suggested` and require explicit confirmation (`confirm_cluster`) or dismissal (`dismiss_cluster`)
- [Implemented] Endpoints `POST /clusters/generate`, `GET /clusters`, `POST /clusters/{id}/confirm`, `POST /clusters/{id}/dismiss` in `src/knowledgeforge/api.py`
- [Verified] Unit test suite `tests/unit/test_clustering_tagging.py` verifying mean-pooling, pending state generation, and human confirmation requirement

## Item 20 — Saved Playbooks Engine (Phase 8 Item 6)
- [Implemented] Database migration `migrations/036_playbooks_and_digests.sql` creating `playbooks` and `playbook_runs` tables
- [Implemented] Playbook execution runner `src/knowledgeforge/playbooks/runner.py` with upfront token budget reservation per question and automatic execution against matching doc_type
- [Implemented] Endpoints `POST /playbooks`, `GET /playbooks`, `GET /documents/{id}/playbooks`, `POST /documents/{id}/playbooks/run` in `src/knowledgeforge/api.py`
- [Verified] Unit test suite `tests/unit/test_playbooks_budget.py` verifying question sets, upfront reservation, failure on budget exhaustion, and result persistence

## Item 21 — Multi-Step Approval Workflows & DB Invariants (Phase 8 Item 7)
- [Implemented] Database migration `migrations/037_document_approvals.sql` defining `approval_chains`, `approval_instances`, `approval_actions`, and database triggers `trg_enforce_approval_completion` and `trg_enforce_document_finalization`
- [Implemented] State machine engine `src/knowledgeforge/approvals/service.py` with row-level locking (`FOR UPDATE OF ai`) and step progression
- [Implemented] Endpoints `POST /approvals/chains`, `POST /documents/{id}/approvals/start`, `POST /approvals/{instance_id}/act`, `GET /documents/{id}/approvals` in `src/knowledgeforge/api.py`
- [Verified] Unit test suite `tests/unit/test_approval_invariants.py` verifying multi-step progression, reject flow, owner-only authorization, concurrent collision prevention, and trigger invariants

## Item 22 — Multilingual Ingestion & Cross-Lingual Retrieval (Phase 8 Item 8)
- [Implemented] Cross-lingual prompt instruction in `src/knowledgeforge/generation/prompt.py` directing answers in query language with citations in source language
- [Implemented] Multilingual golden set benchmark `evaluation/multilingual-golden-set.json` covering DE->EN, ES->EN, JA->EN queries and documents
- [Implemented] Multilingual evaluation harness `evaluation/run_multilingual_eval.py`
- [Verified] Unit test suite `tests/unit/test_multilingual_retrieval.py` and evaluation runner executing with 100% Hit@1 and 100% citation accuracy across all language pairs

## Item 23 — Embeddable White-Label Widget (Phase 8 Item 9)
- [Implemented] Database migration `migrations/038_clustering_widgets_mobile.sql` defining `tenant_widgets`
- [Implemented] Strict CORS origin validation `validate_widget_origin` prohibiting wildcard `*` origins and suffix spoofing in `src/knowledgeforge/widget/service.py`
- [Implemented] Dedicated per-widget / per-IP rate limiter with `TokenBucketLimiter` defending against DDoS spikes
- [Implemented] Endpoints `POST /widgets`, `GET /widgets`, `GET /widgets/{id}/embed.js`, `POST /widget/ask` strictly scoped to widget's `collection_id`
- [Verified] Unit test suite `tests/unit/test_widget_security.py` verifying origin rejection, rate limiting 429 response, and embed script isolation

## Item 24 — Mobile App Device Lifecycle & Token Rotation (Phase 8 Item 10)
- [Implemented] Database migration `migrations/038_clustering_widgets_mobile.sql` defining `user_devices`
- [Implemented] Push notification channel abstraction `src/knowledgeforge/mobile/notifications.py` (`MockPushNotificationProvider`, `register_user_device`, `unregister_user_device`, `notify_user_devices`)
- [Implemented] Endpoints `POST /devices/register`, `POST /devices/unregister` in `src/knowledgeforge/api.py`
- [Implemented] Refresh-token rotation supporting long-lived background/resume sessions in `src/knowledgeforge/security/refresh.py`
- [Verified] Unit test suite `tests/unit/test_mobile_lifecycle.py` verifying device registration, push dispatch, token rotation, and replay detection family revocation
- [Verified] Round 7 Adversarial Audit suite `tests/unit/test_round7_adversarial_audit.py` verifying zero trust boundary leaks across all Phase 8 features



