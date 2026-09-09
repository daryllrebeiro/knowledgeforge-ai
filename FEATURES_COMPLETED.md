# KnowledgeForge AI — Completed Features Tracker

This tracker records each newly completed feature among the 10 core capabilities, along with a summary of its mechanism, schema changes, and test verification.

---

## Progress Overview
- [x] **Feature 1: Spatial Bounding-Box Grounding & Citation Workspace**
- [x] **Feature 2: Self-Reflective Entailment Critic Guardrail**
- [x] **Feature 3: Dynamic Zero-Code Schema Studio**
- [x] **Feature 4: Hybrid NL-to-SQL & TableQA Synthesizer**
- [x] **Feature 5: Multimodal Visual Chunking & VQA**
- [x] **Feature 6: GraphRAG Multi-Hop Entity Engine**
- [x] **Feature 7: Semantic Redline Diff Engine**
- [x] **Feature 8: Proactive Knowledge Drift & Contradiction Auditor**
- [x] **Feature 9: Agentic Deep Research Planner**
- [x] **Feature 10: Zero-Knowledge Privacy Vault**

---

## Completed Feature Summaries

### Feature 1: Spatial Bounding-Box Grounding & Citation Workspace
- **What was built:** Upgraded KnowledgeForge's chunking and citation architecture to retain pixel-accurate bounding box coordinates `[x0, y0, x1, y1]`.
- **Database Schema:** Added `025_spatial_bounding_boxes.sql` introducing `chunks.bounding_boxes JSONB NOT NULL DEFAULT '[]'::jsonb` with GIN indexing.
- **Ingestion & Retrieval:** Extended `extract_pdf_with_boxes` to parse normalized character coordinates from PDF mediaboxes. Updated `chunk_pages`, `store_document`, `store_chunks`, and `retrieve_chunks` to store and query spatial bounding geometry.
- **API Surface:** Extended `CitationResponse` in `/ask` and `/ask/stream` with `highlights: list[CitationHighlight]`. Introduced `GET /documents/{id}/pages/{page_number}/highlights` for client-side PDF.js overlay geometry.
- **Verification:** Unit tests in `tests/unit/test_spatial_bounding_boxes.py` passing 100%.

### Feature 2: Self-Reflective Entailment Critic Guardrail
- **What was built:** Added an active, real-time Natural Language Inference (NLI) verification critic (`EntailmentVerifier`) that decomposes LLM answers into atomic propositions and checks them against source chunk context before returning to the user.
- **Guardrail Enforcement:** Classifies each claim into `ENTAILED`, `CONTRADICTED`, or `UNVERIFIED`. Automatically redacts or replaces contradicted assertions with safety disclosures and computes an overall `grounding_score` ($0.0 - 1.0$).
- **API Surface:** Updated `generate_answer` and `AskResponse` to include `grounding_score: float` and `is_grounded: bool`.
- **Verification:** Unit tests in `tests/unit/test_verifier.py` passing 100%.

### Feature 3: Dynamic Zero-Code Schema Studio
- **What was built:** Built a zero-code runtime schema compiler and registry allowing tenants to define, version, and execute custom structured extraction models (e.g. contracts, NDAs, leases, clinical trials) without code deployments.
- **Database Schema:** Created migration `026_dynamic_schemas.sql` defining `tenant_extraction_schemas` table with versioning and active state index.
- **Dynamic Compilation:** Implemented `compile_json_schema_to_pydantic` in `src/knowledgeforge/extraction/dynamic_schemas.py` using `pydantic.create_model` with type mapping (`string`, `integer`, `number`, `boolean`).
- **Sample Inference:** Built `infer_schema_from_sample` to automatically infer structured schema proposals from sample document text.
- **API Surface:** Added endpoints `POST /admin/schemas`, `GET /admin/schemas`, `GET /admin/schemas/{name}`, and `POST /admin/schemas/infer`.
- **Verification:** Unit tests in `tests/unit/test_dynamic_schemas.py` passing 100%.

### Feature 4: Hybrid NL-to-SQL & TableQA Synthesizer
- **What was built:** Implemented an intent-driven SQL aggregation engine (`TableQASynthesizer`) that bridges unstructured text retrieval with quantitative analytics over extracted JSONB fields.
- **Security & Sandboxing:** Validates queries to ensure strict read-only SELECT execution, enforces tenant isolation at query construction time (`tenant_id = %s`), and rejects data-modifying statements.
- **Aggregation Coverage:** Handles arithmetic sum, average, count, maximum, and minimum calculations with automatic vendor name and currency parsing.
- **API Surface:** Updated `/ask` endpoint to route quantitative intents through `TableQASynthesizer`, returning `sql_executed` and `table_results` in `AskResponse`.
- **Verification:** Unit tests in `tests/unit/test_table_qa.py` passing 100%.

### Feature 5: Multimodal Visual Chunking & VQA
- **What was built:** Expanded document ingestion and storage beyond plaintext into visual figures, architecture diagrams, and financial charts.
- **Database Schema:** Created migration `027_visual_chunks.sql` adding `modality VARCHAR(16) DEFAULT 'text'` and `image_storage_uri TEXT` with partial indexing.
- **Visual Extraction Pipeline:** Implemented `extract_visual_regions` in `src/knowledgeforge/ingestion/extract_visuals.py` to extract embedded figures and bounding polygons from PDF pages.
- **Multimodal Formatting:** Created `format_visual_context_block` and updated `TextChunk` to represent visual elements alongside text for native Gemini Vision reasoning.
- **Verification:** Unit tests in `tests/unit/test_visual_chunks.py` passing 100%.

### Feature 6: GraphRAG Multi-Hop Entity Engine
- **What was built:** Implemented an emergent Knowledge Graph in PostgreSQL for resolving complex multi-hop relational queries spanning interconnected enterprise documents.
- **Database Schema:** Created migration `028_knowledge_graph.sql` introducing `graph_entities` and `graph_relationships` with foreign keys, unique constraints, and traversal indexes.
- **Entity-Relation Extraction:** Implemented `extract_entities_and_relations` and `store_graph_triples` in `src/knowledgeforge/extraction/graph_extractor.py` to identify entities and relation triples (`OWNS`, `SUBSIDIARY_OF`, `SUPPLIES`, etc.).
- **Recursive CTE Traversal:** Implemented `traverse_entity_neighborhood` in `src/knowledgeforge/retrieval/graph_traversal.py` executing multi-hop recursive queries with cycle prevention.
- **API Surface:** Added `POST /graph/query` endpoint for interactive multi-hop neighborhood exploration.
- **Verification:** Unit tests in `tests/unit/test_graph_rag.py` passing 100%.

### Feature 7: Semantic Redline Diff Engine
- **What was built:** Developed an automated document revision and contract redline comparison engine (`compute_clause_diff`) that performs bipartite semantic alignment and risk delta scoring.
- **Clause Alignment & Risk Scoring:** Pairs clauses across document versions, identifies added/removed/altered terms, and detects changes to critical covenants (liability caps, indemnities, warranties, arbitration) with risk severity classification (`CRITICAL`, `MODERATE`, `EDITORIAL`, `NONE`).
- **Database Integration:** Created `diff_documents_from_db` to load chunks for any two documents within a tenant and synthesize executive comparison summaries.
- **API Surface:** Added `POST /documents/diff` returning structured redline matrix and risk metrics.
- **Verification:** Unit tests in `tests/unit/test_diff_engine.py` passing 100%.

### Feature 8: Proactive Knowledge Drift & Contradiction Auditor
- **What was built:** Designed an asynchronous auditing service (`detect_statement_contradictions`) that surfaces conflicting facts and temporal policy drifts across indexed documents in the knowledge base.
- **Database Schema:** Created migration `029_knowledge_conflicts.sql` introducing `knowledge_conflicts` table storing statement pairs, contradiction scores, and resolution states (`OPEN`, `RESOLVED`, `IGNORED`).
- **Contradiction Detection:** Evaluates cross-document assertion pairs using cosine similarity and NLI semantic scoring to detect mutual exclusivity and conflicting business policies.
- **Admin Workflow & API Surface:** Added endpoints `GET /admin/conflicts` to inspect detected drift and `POST /admin/conflicts/{id}/resolve` to allow curators to resolve or dismiss contradictions.
- **Verification:** Unit tests in `tests/unit/test_auditor.py` passing 100%.

### Feature 9: Agentic Deep Research Planner
- **What was built:** Autonomous multi-round recursive query planner and executive dossier synthesizer (`DeepResearchPlanner`) that decomposes complex briefs into multi-modal sub-goals and compiles executive intelligence reports.
- **Database Schema:** Created migration `030_research_jobs.sql` introducing `research_jobs` table storing multi-round goal breakdowns, iterative query execution steps, sources, and final reports.
- **Multi-Engine Investigation & Gap Analysis:** Decomposes queries into strategy-targeted sub-goals (`vector` for semantic context, `graph` for multi-hop entity relationships, `table` for quantitative metrics), evaluates knowledge gaps iteratively, and issues dynamic follow-up investigations.
- **API Surface:** Added endpoints `POST /research/jobs` for executing deep research investigations, `GET /research/jobs` for listing tenant jobs, and `GET /research/jobs/{id}` for retrieving dossiers.
- **Verification:** Unit tests in `tests/unit/test_deep_research.py` passing 100%.

### Feature 10: Zero-Knowledge Privacy Vault
- **What was built:** Built a cryptographic in-line pseudonymization and zero-knowledge privacy engine (`PrivacyVault`) ensuring raw PII/PHI (SSN, credit cards, emails, phone numbers, and named entities) never reach external LLM models or unencrypted storage.
- **Database Schema:** Created migration `031_privacy_vault.sql` introducing `pseudonym_vault` table with tenant isolation and unique constraint on `(tenant_id, surrogate_token)`.
- **Deterministic Pseudonymization & Encryption:** Implemented deterministic HMAC-SHA256 surrogate token generation (e.g. `[EMAIL_8c2b4f10]`), preserving entity co-reference and relationships across queries and documents within a tenant. Stored raw values under tenant-scoped authenticated Fernet/AES encryption.
- **API Surface:** Added endpoints `POST /privacy/mask` for in-line redaction, `POST /privacy/unmask` for authorized re-hydration, and `GET /privacy/vault` for auditing masked surrogate classifications.
- **Verification:** Unit tests in `tests/unit/test_privacy_vault.py` passing 100%.


