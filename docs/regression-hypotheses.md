# Regression Hypotheses for Round 3 Fixes

This document captures the specific regression hypothesis for each fix implemented in Round 3. These should be checked in Round 4 and all future audits.

---

## R1 (RA4): HNSW Migration Runner Autocommit

**Fix:** Added `.concurrent.sql` naming convention; runner executes these migrations with `autocommit=True` outside the transaction wrapper.

**Regression Hypothesis:** A future migration that needs `CONCURRENTLY` but is NOT named with `.concurrent.sql` suffix will fail with "CREATE INDEX CONCURRENTLY cannot run inside a transaction block".

**Check in Round 4:** Verify all migrations requiring `CONCURRENTLY` have the `.concurrent.sql` suffix, and no regular migration was accidentally renamed.

---

## R2 (RA7): Last-Owner Guard Advisory Lock

**Fix:** `ensure_owner_remaining()` acquires `pg_advisory_xact_lock(hashtext(tenant_id))` at start.

**Regression Hypothesis:** 
1. A future membership role-change endpoint calls `ensure_owner_remaining()` but does NOT wrap the call in the same transaction as the actual role change (the lock is transaction-scoped).
2. A future endpoint uses a different locking mechanism (e.g., `SELECT FOR UPDATE` on membership rows) that doesn't compose with the advisory lock, creating deadlock risk.

**Check in Round 4:** Verify any new membership mutation endpoints wrap `ensure_owner_remaining()` and the role change in a single transaction.

---

## R3 (RA3): Redis Key Environment Isolation

**Fix:** All breaker and limiter keys now use `make_redis_key()` which prefixes with `knowledgeforge:{environment}:`.

**Regression Hypothesis:**
1. A new Redis-backed component (rate limiter, breaker, budget counter, cache) is added that hardcodes `"knowledgeforge:..."` without going through `make_redis_key()`.
2. The `environment` setting is not set correctly in some deployment, causing keys to collide across environments anyway.

**Check in Round 4:** Search codebase for hardcoded `"knowledgeforge:"` string literals in Redis key construction; all should use `make_redis_key()` or equivalent.

---

## R4 (RG2): PPTX XXE/Billion Laughs Pre-Scan

**Fix:** Scans all `.xml` and `.rels` entries in the PPTX zip for `<!DOCTYPE` and `<!ENTITY` before calling `Presentation()`.

**Regression Hypothesis:**
1. A malicious PPTX uses a different file extension for XML entries (not `.xml` or `.rels`) that still gets parsed by `lxml` internally.
2. The scan is bypassed by encoding the entity declaration in a way that the byte-search misses (e.g., UTF-16, base64 within XML).
3. A future extractor for another ZIP-based format (e.g., ODT, XLSX) is added without the same pre-scan.

**Check in Round 4:** Verify the scan covers all entries that `python-pptx`/`lxml` might parse; verify any new ZIP-based extractors have equivalent pre-scans.

---

## R5 (RD1): Per-Tenant Daily Token Budget

**Fix:** `RedisBudgetCounter` with atomic check-and-reserve + reconcile for `/ask` endpoints.

**Regression Hypothesis:**
1. The pre-flight `estimate_token_cost()` significantly underestimates actual usage, allowing a tenant to exceed the budget before reconciliation catches it.
2. The reconciliation fails silently (Redis error) and the budget is not corrected, allowing continued over-spend.
3. A new Gemini call path (e.g., a new endpoint) is added that doesn't check the token budget.

**Check in Round 4:** Verify all Gemini token-consuming endpoints check `get_token_budget()`; verify reconciliation error handling.

---

## R6 (RD2): Per-Tenant Daily Extraction Budget

**Fix:** `get_extraction_budget().check_and_reserve(tenant_id, 1)` in reprocess endpoint and worker pipeline.

**Regression Hypothesis:**
1. A new extraction trigger path (e.g., webhook, scheduled job, different API endpoint) bypasses the budget check.
2. The worker pipeline check is inside the transaction but after chunk storage, so failed extractions still count against budget (or don't, inconsistently).
3. Extraction retries (the bounded retry in pipeline) count as separate budget units or don't count at all.

**Check in Round 4:** Verify all extraction job creation paths check the budget; verify retry behavior is intentional.

---

## R7 (RB5): API Key last_used_at Throttling

**Fix:** `UPDATE ... SET last_used_at = now() WHERE key_hash = %s AND (last_used_at IS NULL OR last_used_at < now() - interval '1 hour')`

**Regression Hypothesis:**
1. The 1-hour threshold is too coarse for security auditing needs (e.g., detecting compromised key usage within minutes).
2. A future feature needs precise `last_used_at` and the throttling causes confusion.

**Check in Round 4:** Verify the 1-hour threshold is still appropriate; verify any new features needing precise last-use timestamps have their own tracking.

---

## R8 (RG1): Dependency Pinning and Vulnerability Scanning

**Fix:** CI workflow (`.github/workflows/security.yml`) runs `pip-audit` on every PR/push.

**Regression Hypothesis:**
1. `pip-audit` is not run in CI for some workflow (e.g., only on push, not PR; or only on certain branches).
2. A dependency with a known vulnerability is added but the CI passes because the vulnerability database wasn't updated.
3. The lockfile (`uv.lock`) is not kept in sync with `pyproject.toml`, allowing drift.

**Check in Round 4:** Verify `pip-audit` runs on all CI workflows; verify lockfile is committed and `uv sync --frozen` is used.

---

## R9 (RE3): Magic-Byte File Type Detection

**Fix:** `_detect_file_type()` validates magic bytes against extension; rejects mismatches.

**Regression Hypothesis:**
1. A legitimate file with non-standard magic bytes (e.g., PDF with non-standard header, truncated image) is rejected incorrectly.
2. A new supported file type is added without adding its magic bytes to `_MAGIC_BYTES`.
3. The UTF-8 check for text files rejects valid non-UTF-8 text files (e.g., Latin-1 encoded CSV).

**Check in Round 4:** Verify all supported file types have magic byte entries; test with edge-case legitimate files.

---

## R10 (RH1): GCS Bucket IAM Verification

**Fix:** Documented verification checklist in `docs/infrastructure-verification.md`.

**Regression Hypothesis:** Infrastructure changes (Terraform apply) widen bucket permissions or service account roles without re-running the verification checklist.

**Check in Round 4:** Verify infrastructure changes include re-running the GCS bucket verification checklist; verify the checklist is in the infra repo's CI/CD.

---

## Process Regression Hypothesis (Meta)

**Observation:** Round 3 found 3 regressions introduced by Round 2 fixes (RA4, RA7, RA3).

**Hypothesis:** Future rounds will continue to find regressions unless each fix has its regression hypothesis documented BEFORE merge and checked in the next audit round.

**Check in Round 4:** Verify this document exists and was used to guide Round 4's audit scope.