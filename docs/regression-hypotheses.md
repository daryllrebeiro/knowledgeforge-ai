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

## R11 (R4-Fix1): DB Trigger for Owner-Count Invariant

**Fix:** Migration 019 creates deferred constraint triggers `trg_enforce_owner_count` and `trg_enforce_owner_on_insert` on `tenant_memberships` in addition to application-level advisory locks.

**Regression Hypothesis:**
1. A bulk data migration or admin script uses `ALTER TABLE tenant_memberships DISABLE TRIGGER ALL;` or drops the trigger during maintenance and fails to re-enable it.
2. A new membership mutation path (e.g. org merging, user deactivation) bypasses the trigger or is executed in a context that suppresses exceptions.

**Check in future audits:** Verify `trg_enforce_owner_count` and `trg_enforce_owner_on_insert` remain active in `information_schema.triggers` and integration tests pass.

---

## R12 (R4-Fix2): OOXML XXE and Zip-Bomb Guard via defusedxml

**Fix:** `extract_docx.py` and `extract_pptx.py` use `defusedxml.ElementTree.iterparse(..., forbid_dtd=True)` on all `.xml`/`.rels` package entries, and enforce XML entry size limits and zip-bomb ratio checks.

**Regression Hypothesis:**
1. Someone changes `iterparse` invocation without specifying `forbid_dtd=True` (which defaults to `False` in `defusedxml.ElementTree.iterparse`), allowing DTD attacks.
2. A new parser for another XML-based or zip-based format (e.g. XLSX, ODT, SVG) is added that parses directly with standard library `xml` or `lxml` without defusedxml validation.

**Check in future audits:** Verify all XML parsers use `defusedxml` with `forbid_dtd=True` and run unit tests with crafted malicious OOXML packages.

---

## R13 (R4-Fix3): Redis Budget Reservation Release & TTL Backstop

**Fix:** `RedisBudgetCounter` implements `release_reservation` on failure and TTL backstop via `math.min(reservation_ttl, ttl)` to prevent quota exhaustion from failed calls.

**Regression Hypothesis:**
1. A new LLM endpoint or worker task reserves budget but fails to wrap execution in `try ... except` that calls `release_reservation()`.
2. A code path sets `reservation_ttl` to 0 or negative, causing instant expiration of the budget counter key.

**Check in future audits:** Audit all `check_and_reserve` call sites to ensure paired `reconcile()` on success and `release_reservation()` on exception.

---

## R14 (R4-Fix4): Global Registration Rate Limiting

**Fix:** `/auth/register` applies global hourly rate limit (`registration_rate_limit_per_hour`) alongside per-IP limits to prevent tenant-farming Sybil attacks.

**Regression Hypothesis:**
1. A new tenant onboarding flow (e.g. OAuth signup, invite acceptance, CLI registration) is introduced without the global rate limit check.
2. An outage in Redis causes registration rate limiter to fail open instead of failing back to bounded memory local rate limiter.

**Check in future audits:** Verify every tenant creation code path checks `registration_rate_limit_per_hour`.

---

## R15 (R4-Fix5): Circuit Breaker Streaming Recovery

**Fix:** `CircuitBreaker.record_success()` resets `self.opened_at = None` in addition to `self.failures = 0`.

**Regression Hypothesis:**
1. Streaming endpoints catch errors internally without invoking `record_failure()`, leaving breaker blind to streaming upstream failures.
2. Generator disconnection or client aborts are counted as provider failures, prematurely tripping the breaker.

**Check in future audits:** Verify streaming handlers call `record_success()` on completion and only call `record_failure()` on provider exceptions.

---

## Process Regression Hypothesis (Meta)

**Observation:** Round 3 and Round 4 found multiple regressions and untested edge cases introduced by prior fixes (e.g., `forbid_dtd=False` default, `CircuitBreaker.opened_at` not reset on success).

**Hypothesis:** Future rounds will continue to find regressions unless each fix has its regression hypothesis documented BEFORE merge and verified with negative/adversarial tests.

**Check in future audits:** Verify this document is maintained and updated on every phase transition.