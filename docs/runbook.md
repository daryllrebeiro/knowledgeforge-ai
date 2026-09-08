# KnowledgeForge Production Operations Runbook

**Revision:** Phase 4 (2026-09-08)  
**Audience:** On-call engineers, SRE, and platform administrators  
**Scope:** Production and staging environments for KnowledgeForge API and background workers.

---

## 1. Quick Reference: Dashboards & Diagnostic Queries

| Component | Target / URL | Primary Query / Filter |
|---|---|---|
| **API Cloud Run** | Cloud Monitoring -> Cloud Run -> `knowledgeforge-api-${ENV}` | `resource.type="cloud_run_revision" AND resource.labels.service_name="knowledgeforge-api-${ENV}"` |
| **Worker Cloud Run** | Cloud Monitoring -> Cloud Run -> `knowledgeforge-worker-${ENV}` | `resource.type="cloud_run_revision" AND resource.labels.service_name="knowledgeforge-worker-${ENV}"` |
| **Extraction Worker** | Cloud Monitoring -> Cloud Run -> `knowledgeforge-extraction-${ENV}` | `resource.type="cloud_run_revision" AND resource.labels.service_name="knowledgeforge-extraction-${ENV}"` |
| **Pub/Sub Subscriptions** | Cloud Monitoring -> Pub/Sub -> Subscriptions | `resource.labels.subscription_id=~"knowledgeforge-.*-dead-letter-sub.*"` |
| **Structured Logs** | Cloud Logging -> Logs Explorer | `jsonPayload.logger=~"knowledgeforge.*" OR jsonPayload.event=~".*"` |

---

## 2. Alert Playbooks

### Alert 1: API 5xx Error Rate Spike (`api_error_rate` > 2% over 5m)

- **Severity:** High
- **Description:** More than 2% of client requests over a 5-minute window returned HTTP 5xx responses.
- **First 3 Diagnostic Steps:**
  1. **Check Cloud Logging for error distribution:**
     ```
     resource.type="cloud_run_revision"
     resource.labels.service_name="knowledgeforge-api-production"
     severity>=ERROR
     ```
     Identify if errors are clustered on specific routes (e.g. `/ask`, `/documents`, `/auth/login`).
  2. **Differentiate Database vs External Provider vs App Failures:**
     - `psycopg.OperationalError` / connection timeout -> Database connection pool exhausted or Cloud SQL unavailable.
     - `HTTPException(503, "Budget service unavailable")` -> Redis unreachable.
     - `CircuitOpenError` -> Upstream Gemini outage or rate-limiting.
  3. **Inspect Active Database Connections & Cloud SQL CPU:**
     Check Cloud SQL metrics in Cloud Console: CPU utilization, active connections, storage capacity.
- **Remediation:**
  - If Redis is down: verify Memorystore / Redis instance health. Redis rate limiter fails back to in-memory local limiter, but budget checks fail closed to protect spend.
  - If Cloud SQL connections are maxed: scale up API instances gradually or check for connection leaks in recent deploy.
  - If code defect: roll back Cloud Run revision to last known good revision:
    `gcloud run services update-traffic knowledgeforge-api-production --to-revisions=PREVIOUS_REVISION=100`

---

### Alert 2: API P95 Latency Breach (`api_latency` > 5000ms over 5m)

- **Severity:** Medium
- **Description:** 95th percentile latency of incoming API requests exceeded 5 seconds.
- **First 3 Diagnostic Steps:**
  1. **Query request logs for latency breakdown:**
     ```
     resource.type="cloud_run_revision"
     jsonPayload.route="/ask"
     jsonPayload.latency_ms > 4000
     ```
     Check if the slowdown is in retrieval (`retrieve_chunks` pgvector cosine search) or generation (Gemini response stream).
  2. **Inspect pgvector query performance:**
     Run `EXPLAIN ANALYZE` on vector queries in database. Verify HNSW index is active (`idx_chunks_embedding_hnsw`) and not falling back to sequential scans.
  3. **Check Gemini API status & latency:**
     Review Gemini API dashboard for regional latency spikes or throttles.
- **Remediation:**
  - If pgvector query plans are slow: trigger REINDEX on HNSW index concurrently (using migration 015 convention).
  - If Gemini generation is slow: check if prompt context is oversized or consider lowering `CONVERSATION_HISTORY_TURNS`.

---

### Alert 3: Ingestion Dead-Letter Queue Non-Empty (`dead_letter_depth` > 0)

- **Severity:** High
- **Description:** Messages failed processing after max delivery attempts (5 retries) and landed in `knowledgeforge-ingestion-dead-letter`.
- **First 3 Diagnostic Steps:**
  1. **Pull and inspect DLQ messages without acknowledging:**
     ```bash
     gcloud pubsub subscriptions pull knowledgeforge-ingestion-dead-letter-sub-production --limit=5 --auto-ack=false
     ```
     Extract `document_id`, `tenant_id`, and `storage_uri`.
  2. **Correlate with worker logs:**
     ```
     jsonPayload.event="job.failure"
     jsonPayload.document_id="<FAILED_DOCUMENT_ID>"
     ```
     Check the exact exception: PDF extraction crash, XXE guard rejection, zip bomb, or GCS 404.
  3. **Check `failed_ingestions` table:**
     ```sql
     SELECT * FROM failed_ingestions WHERE document_id = '<FAILED_DOCUMENT_ID>';
     ```
- **Remediation:**
  - If malformed/malicious file: document rejection is intentional; verify tenant status is `failed` and dismiss.
  - If transient provider failure: fix underlying issue and trigger re-ingestion via POST `/documents/{id}/reprocess`.

---

### Alert 4: Extraction Dead-Letter Queue Non-Empty (`extraction_dead_letter_depth` > 0)

- **Severity:** Medium
- **Description:** Structured extraction jobs failed max retries and entered `knowledgeforge-extraction-dead-letter`.
- **First 3 Diagnostic Steps:**
  1. **Inspect extraction DLQ payload:**
     `gcloud pubsub subscriptions pull knowledgeforge-extraction-dead-letter-sub-production --limit=5 --auto-ack=false`
  2. **Query extraction worker logs:**
     `jsonPayload.logger="knowledgeforge.extraction.worker" AND jsonPayload.event="extraction.job.failure"`
  3. **Check `document_extractions` status:**
     Verify if failure was due to OCR failure, schema validation error, or Gemini schema mismatch.
- **Remediation:**
  - If schema mismatch: check if new document format requires updated field extraction prompts or fallback parsing.
  - Requeue job after fix.

---

### Alert 5: Circuit Breaker Tripped (`circuit_breaker_open`)

- **Severity:** High
- **Description:** Repeated provider call failures reached threshold (default 3) and opened the circuit breaker.
- **First 3 Diagnostic Steps:**
  1. **Identify which circuit breaker tripped:**
     - `knowledgeforge:production:breaker:gemini:embedding`
     - `knowledgeforge:production:breaker:gemini:generation`
     - `knowledgeforge:production:breaker:gemini:rewrite`
  2. **Check Gemini API status and error responses:**
     Look for 429 Too Many Requests (quota exhaustion) or 503 Service Unavailable in outbound provider logs.
  3. **Check if fallback mode is active:**
     Verify whether requests are failing fast with HTTP 503 rather than hanging for 30s timeouts.
- **Remediation:**
  - Circuit automatically resets to half-open after `GEMINI_BREAKER_RECOVERY_SECONDS` (30s).
  - If quota exceeded: request Gemini quota increase or enable fallback keys.
  - If outage is prolonged: notify users via status page.

---

### Alert 6: Platform Daily Spend Ceiling Approaching (`platform_spend_ceiling`)

- **Severity:** Critical
- **Description:** Total platform token usage has reached 80% of `platform_daily_token_budget`.
- **First 3 Diagnostic Steps:**
  1. **Query `tenant_usage_daily` to find high-spend tenants:**
     ```sql
     SELECT tenant_id, sum(total_tokens) as tokens, count(*) as calls
     FROM request_logs
     WHERE timestamp >= CURRENT_DATE
     GROUP BY tenant_id ORDER BY tokens DESC LIMIT 10;
     ```
  2. **Determine if spend is legitimate organic surge or runaway automation:**
     Check request frequency, user agents, and IP distribution for top tenant.
  3. **Check per-tenant budgets:**
     Verify if individual tenant budget limits were bypassed or configured excessively high.
- **Remediation:**
  - If runaway tenant: throttle or temporarily suspend the offending tenant via admin console.
  - If legitimate platform growth: dynamically increase `platform_daily_token_budget` in Secret Manager / environment configuration.

---

## 3. Incident Diagnostics Cheat Sheet

```bash
# Check API service status
gcloud run services describe knowledgeforge-api-production --region=asia-south1

# Stream real-time structured logs
gcloud logging tail "resource.type=cloud_run_revision AND resource.labels.service_name=knowledgeforge-api-production"

# Check subscription backlog
gcloud pubsub subscriptions describe knowledgeforge-ingestion-worker-production --format="value(numUndeliveredMessages)"

# Test database connectivity from Cloud Shell
gcloud sql connect knowledgeforge-production --user=knowledgeforge --database=knowledgeforge
```

---

## 4. SLO Targets & Review Cadence

- **`/ask` Availability:** 99.0% (rolling 30 days)
- **`/ask` Latency:** P95 < 5,000ms
- **20-page Document Ingestion:** P95 time-to-ready < 120s
- **Post-Mortem Policy:** Any Sev-1/Sev-2 incident triggering customer-facing downtime requires an RCA within 48 hours referencing `docs/regression-hypotheses.md`.

---

## 5. Disaster Recovery & Business Continuity

### 5.1 Recovery Objectives (RTO & RPO)

| Layer | Component | Backup Mechanism | Target RPO | Target RTO | Failure Impact |
|---|---|---|---|---|---|
| **Primary Relational & Vector DB** | Cloud SQL (PostgreSQL 17 + pgvector) | Automated daily snapshots + Continuous Write-Ahead Log (WAL) archiving (PITR, 7-day retention) | **< 5 seconds** | **< 20 minutes** | Read/write outage until restored to new instance or failover replica. |
| **Object Storage** | Google Cloud Storage (Bucket `uploads`) | Versioning enabled + 30-day noncurrent version lifecycle retention | **0 seconds** (sync dual-region) | **< 5 minutes** | Raw file download/reprocessing degraded; extracted data and embeddings in DB remain queryable. |
| **Stateful Cache & Limiters** | Memorystore for Redis | In-memory ephemeral with optional AOF persistence | **N/A** (ephemeral) | **< 2 minutes** (cold restart) | Rate limiters fall back to per-instance in-memory buckets; budget checks fail closed to prevent overspend. |

### 5.2 Redis Data-Loss Impact & Acceptance (Design Invariant)

When Redis is flushed, partitioned, or cold-restarted:
1. **Rate Limiting Degradation**: `RedisTokenBucketLimiter` falls back to local in-process limiter (`TokenBucketLimiter`), keeping API containers protected from brute-force spikes.
2. **Budget Metering Behavior**:
   - `RedisBudgetCounter` sliding window keys reset to zero. Tenants obtain a fresh daily token and extraction allocation.
   - **Hard Platform Stop**: Platform-level spend remains bounded by the global `platform_daily_token_budget` when populated, and Stripe billing records in Cloud SQL remain pristine.
   - **Fail-Closed Protection**: If Redis is unreachable, `check_and_reserve()` immediately returns `(False, "Budget service unavailable")`, preventing unmetered leakage.
3. **Circuit Breakers**: Reset to `CLOSED` state, enabling natural re-probing of external LLM endpoints.

### 5.3 Cloud SQL Point-in-Time Recovery (PITR) Restore Drill Procedure

Quarterly disaster recovery validation procedure executed in staging/sandbox:

1. **Select Target Restoration Timestamp**:
   ```bash
   TARGET_TIME=$(date -u -d '2 hours ago' +%Y-%m-%dT%H:%M:%SZ)
   echo "Testing restoration to: $TARGET_TIME"
   ```

2. **Clone Database to Disaster Recovery Validation Instance**:
   ```bash
   gcloud sql instances clone knowledgeforge-production knowledgeforge-pitr-drill \
       --point-in-time="$TARGET_TIME" \
       --project="$GCP_PROJECT_ID"
   ```

3. **Obtain Connection String & Execute Automated Integrity Check**:
   ```bash
   export RESTORE_DATABASE_URL="postgresql://knowledgeforge:${DB_PASS}@10.x.x.x:5432/knowledgeforge"
   export DATABASE_URL="postgresql://knowledgeforge:${PROD_DB_PASS}@10.y.y.y:5432/knowledgeforge"
   
   python scripts/backup_restore_check.py
   ```

4. **Verify Relational & Vector Invariants**:
   The verification script validates parity across:
   - `documents` row count and statuses
   - `chunks` row count and pgvector `embedding IS NOT NULL` consistency
   - `document_extractions` structured JSON integrity
   - `users`, `tenants`, and `conversations` counts

5. **Decommission Validation Instance**:
   ```bash
   gcloud sql instances delete knowledgeforge-pitr-drill --project="$GCP_PROJECT_ID" --quiet
   ```

### 5.4 Automated Backup Verification & Alerting

Cloud Monitoring monitors the metric `cloudsql.googleapis.com/database/backup/latest_backup_time`. An alert is triggered if `currentTime - latest_backup_time > 26 hours`, ensuring silent backup failures are caught before standard operational windows expire.

