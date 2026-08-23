# Phase 15 deployment gate

Phase 15 is the first phase that intentionally requires an authenticated GCP project.
No account, billing project, credential, or deployment is created by local development.

## Required inputs

- GCP project with billing enabled
- Authenticated `gcloud` or GitHub Workload Identity
- Enabled APIs: Cloud Run, Cloud SQL Admin, Storage, Pub/Sub, Secret Manager, Monitoring
- Artifact Registry location
- Gemini API key
- Database password and JWT secret
- Approved staging region and environment name

## Execution order

1. Run `scripts/staging_preflight.ps1`.
2. Review Terraform variables and run `terraform plan` for staging.
3. Apply Terraform and capture outputs.
4. Apply migrations 001 through 007 to Cloud SQL.
5. Deploy API and worker images through `.github/workflows/deploy.yml`.
6. Verify Secret Manager access without printing secret values.
7. Run authenticated register, upload, status, ask, duplicate, delete, and account-delete
   smoke tests.
8. Force a worker failure and verify failed status plus dead-letter delivery.
9. Run backup restore, Locust, and monitoring alert checks.
10. Repeat the smoke test through the production approval gate.

## Current gate status

The account-free prerequisites are implemented and locally statically validated.
Execution is blocked until the project, billing, credentials, and secret values are
intentionally supplied. This is an external-state blocker, not a missing local code
artifact.
