# Account-agnostic security hardening

The application can be hardened and tested without creating users or attaching cloud
accounts. The following controls are implemented locally and remain enabled by default:

- Authentication endpoints are rate-limited by a deterministic caller-address key.
- A Redis-backed atomic token bucket is available through `REDIS_URL`; empty
  configuration keeps the safe single-process limiter for local development.
- Tenant routes continue to derive tenant scope only from the verified JWT claims.
- Browser cross-origin access is disabled unless `CORS_ALLOWED_ORIGINS` is explicitly
  configured with a comma-separated allowlist.
- API routes are available under `/v1` while the original paths remain compatible.
- Request IDs are returned in `X-Request-ID` and included in structured request logs.
- API responses include baseline browser security headers (`nosniff`, frame denial,
  referrer restriction, and a restrictive permissions policy).
- Cloud configuration is read from environment settings; no login or account bootstrap
  runs during application startup.

## Deferred controls

Live Redis deployment, real Cloud Monitoring, container registry scanning, secret manager
access, live database isolation tests, backup restore drills, and cloud IAM verification
require external infrastructure and are tracked in Phase 8.5.

## Local checks

Run the following without credentials:

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
terraform fmt -check -recursive infrastructure/terraform
```
