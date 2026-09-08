# KnowledgeForge AI — API Versioning & Deprecation Policy

**Version:** 1.0.0  
**Effective Date:** 2026-09-08  
**Status:** Active  

---

## 1. Overview & Versioning Strategy

KnowledgeForge AI provides high-reliability, tenant-isolated Retrieval-Augmented Generation (RAG) and structured document extraction APIs. To ensure enterprise integrations remain rock-solid across platform iterations, this document formalizes our API versioning, deprecation, sunset, and contract enforcement policies.

### 1.1 URI Path Versioning
All public REST API endpoints are explicitly versioned under the `/v1/` path prefix:
- Primary versioned URI format: `https://api.knowledgeforge.ai/v1/{resource}`
- Legacy root path alias: `https://api.knowledgeforge.ai/{resource}` is supported as an alias to `/v1/{resource}` for backward compatibility.
- New integrations **must** target `/v1/`.

### 1.2 Major Version Lifecycle
- **`/v1` (Current Stable)**: Fully supported production contract.
- **`/v2` (Future Breaking)**: Introduced only when incompatible structural changes are required. `/v1` and `/v2` will run concurrently during the migration window.

---

## 2. Backward Compatibility Guarantees

Within a major version (e.g., `/v1`), KnowledgeForge adheres strictly to additive-only changes:

### 2.1 Permitted Non-Breaking Changes
- Adding new endpoints to `/v1/`.
- Adding new optional fields to request bodies or query parameters.
- Adding new fields to response payloads.
- Adding new optional headers.

### 2.2 Client Invariant Expectation
API consumers are expected to follow the **Robustness Principle (Postel's Law)**:
- Clients must ignore unrecognized fields in response JSON payloads.
- Clients must parse dates formatted according to ISO 8601 / RFC 3339.
- Clients must gracefully handle new HTTP status codes or error messages.

### 2.3 Prohibited Changes Within Major Version
- Removing or renaming endpoints.
- Removing or renaming fields in existing request or response payloads.
- Changing field types (e.g., changing an integer ID or UUID string to an object).
- Making previously optional request parameters mandatory.

---

## 3. Deprecation & Sunset Process

When an endpoint, parameter, or schema is slated for retirement:

### 3.1 Advance Notice Windows
- **Minor Field / Endpoint Deprecation**: Minimum **90 calendar days** advance notice before removal.
- **Major API Version Sunset (e.g. `/v1` retirement)**: Minimum **180 calendar days** advance notice before discontinuation.

### 3.2 Standard RFC 8594 Sunset Headers
During the deprecation window, all responses from the deprecated endpoint will include standardized HTTP response headers:
```http
Deprecation: true
Sunset: Tue, 09 Mar 2027 00:00:00 GMT
Link: <https://docs.knowledgeforge.ai/migrations/v1-to-v2>; rel="sunset"
```
- `Deprecation`: Signals that the endpoint or response representation is deprecated.
- `Sunset`: Specifies the exact future timestamp when access will be terminated.
- `Link` (with `rel="sunset"`): Provides the direct URL to the migration and remediation guide.

### 3.3 Communication Channels
- Published in platform release notes and the Developer Portal.
- Direct email notifications sent to tenant account owners and registered technical contacts.
- Warning notices displayed in the KnowledgeForge Admin Console.

---

## 4. OpenAPI Contract Enforcement & CI Drift Detection

To guarantee that the deployed code matches documentation without silent regressions:
1. **Committed Machine-Readable Spec**: The authoritative OpenAPI 3.1 specification is committed to version control at [`docs/openapi.json`](file:///c:/Users/Lenovo%20Laptop/dev/knowledgeforge-ai/docs/openapi.json).
2. **Automated CI Validation**:
   - `scripts/export_openapi.py --check` executes on every pull request.
   - Unit test suite [`tests/unit/test_openapi_contract.py`](file:///c:/Users/Lenovo%20Laptop/dev/knowledgeforge-ai/tests/unit/test_openapi_contract.py) verifies:
     - 100% schema agreement with zero uncommitted drift.
     - Presence of `/v1` prefixed route aliases.
     - Presence of all core domain request/response data contracts.
3. **Drift Remediation**: If route or schema changes are made intentionally, the developer must regenerate the specification with `python scripts/export_openapi.py` and commit the updated `docs/openapi.json` as part of the PR.

---

## 5. Standard Error Contract

All error responses from KnowledgeForge APIs return consistent, structured JSON payloads:
```json
{
  "detail": "Descriptive human-readable explanation of error or validation failure"
}
```

Standard Status Codes:
- `400 Bad Request`: Malformed syntax, invalid JSON, or invalid business payload.
- `401 Unauthorized`: Missing, expired, or invalid authentication credentials.
- `403 Forbidden`: Insufficient role (e.g. tenant member attempting owner/admin action) or cross-tenant access violation.
- `404 Not Found`: Target resource does not exist within the authenticated tenant's scope.
- `409 Conflict`: Unique constraint violation (e.g. email already registered, duplicate document upload).
- `413 Payload Too Large`: Upload exceeds size limits (e.g. 100MB document ceiling).
- `429 Too Many Requests`: Rate limit or daily token/extraction budget exceeded.
- `500 Internal Server Error`: Unexpected unhandled server condition.
- `503 Service Unavailable`: Circuit breaker open or dependent backing service temporarily unavailable.
