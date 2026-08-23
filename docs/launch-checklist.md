# Launch checklist

- [ ] Apply Terraform in a clean staging project.
- [ ] Run all migrations, including pgvector and tenant isolation migrations.
- [ ] Register a staging account and verify authenticated upload/query flows.
- [ ] Upload a duplicate document and verify idempotent handling.
- [ ] Force a worker failure and verify dead-letter delivery and `failed` status.
- [ ] Restore a Cloud SQL backup into a temporary instance and verify a query.
- [ ] Run the Locust scenario and record the concurrency ceiling and SLO results.
- [ ] Configure Gemini, database, and JWT secrets through Secret Manager.
- [ ] Add a support contact and privacy/terms pages before accepting real user data.
- [ ] Define incident communication and rollback owners.
