# Infrastructure Verification Checklist (Fix R10 - RH1)

## GCS Bucket IAM/ACL Configuration

**Status:** NOT VERIFIED - Terraform not in repository

The Round 3 adversarial audit could not verify the GCS bucket configuration because the infrastructure-as-code (Terraform) is not in this repository.

### Required Verification

Whoever has access to the infrastructure-as-code repo must confirm directly:

1. **Bucket is not publicly readable**
   - Test: `gsutil acl get gs://<bucket-name>` should not show `allUsers` or `allAuthenticatedUsers` with READ permissions
   - Test: `curl https://storage.googleapis.com/<bucket-name>/<known-object>` should return 403/404 without credentials

2. **Service account has minimum necessary permissions**
   - The API/worker service account should have ONLY:
     - `roles/storage.objectCreator` on this specific bucket
     - `roles/storage.objectViewer` on this specific bucket
     - `roles/storage.objectDeleter` on this specific bucket (if deletion is used)
   - NOT project-level roles like `roles/storage.admin`, `roles/storage.objectAdmin`, or `roles/storage.legacyBucketOwner`

3. **Verification commands**
   ```bash
   # Check bucket IAM policy
   gcloud storage buckets get-iam-policy gs://<bucket-name>

   # Check service account permissions
   gcloud projects get-iam-policy <project-id> --flatten="bindings[].members" --filter="bindings.members:<service-account-email>" --format="table(bindings.role)"

   # Test unauthenticated access (should fail)
   curl -I https://storage.googleapis.com/<bucket-name>/<known-object>
   ```

### Current State
- Repository uses `fsouza/fake-gcs-server` for local development (docker-compose.full.yml)
- Production GCS bucket configuration is in a separate infrastructure repo
- Service account used by API/worker is configured via `GCP_PROJECT_ID` and `GCS_BUCKET` env vars

### Action Required
Before considering Round 3 remediation complete, the infrastructure team must run the verification checklist above and document the results.