#!/usr/bin/env bash
# =============================================================================
# KnowledgeForge AI: One-Command Google Cloud Run Deployment (Bash)
# Modeled after the support-master deploy pattern (commit-SHA images, secrets
# injected out-of-band, live health verification), adapted for KnowledgeForge's
# four runtimes: API, ingestion worker, extraction worker, outbox dispatcher.
#
# Free-tier friendly: Cloud Run, Cloud Build, Artifact Registry, Secret Manager,
# Pub/Sub, Cloud Scheduler, and Cloud Storage all have no-cost tiers. Postgres
# must be supplied via DATABASE_URL (free options with pgvector: Neon, Supabase).
#
# Required environment variables:
#   DATABASE_URL    a reachable PostgreSQL+pgvector connection string
#   GEMINI_API_KEY  a Gemini API key
# Optional environment variables:
#   JWT_SECRET_KEY  existing secret to reuse (generated on first run otherwise)
#   REDIS_URL       shared rate-limit Redis (omit for single-instance in-memory)
#   ROTATE_SECRETS  "true" to add new secret versions when values change;
#                   default refuses and prints rotation instructions
#   GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_REGION / KNOWLEDGEFORGE_ENVIRONMENT
#
# What this script does (idempotent — safe to re-run):
#   [1/10] Preflight: gcloud, auth, project, required env, uv for migrations
#   [2/10] Enable required Google Cloud APIs
#   [3/10] Artifact Registry + one Cloud Build submission (API + worker images,
#          tagged with the current commit SHA and :latest)
#   [4/10] Secret Manager: create, verify, or explicitly rotate secrets
#   [5/10] GCS bucket + Pub/Sub topics (ingestion + extraction + dead-letters)
#   [6/10] Dedicated runtime/push/scheduler service accounts + least-privilege IAM
#   [7/10] Deploy ingestion worker, extraction worker, outbox Cloud Run Job,
#          Pub/Sub push subscriptions (OIDC), Cloud Scheduler trigger
#   [8/10] Apply database migrations 001+ from this checkout
#   [9/10] Deploy the public API (ASYNC_INGESTION=true)
#   [10/10] Health + functional smoke test (register/upload/extract/ask/delete)
# =============================================================================

set -euo pipefail

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-${1:-}}"
REGION="${GOOGLE_CLOUD_REGION:-${2:-us-central1}}"
ENVIRONMENT="${KNOWLEDGEFORGE_ENVIRONMENT:-staging}"
ROTATE_SECRETS="${ROTATE_SECRETS:-false}"

API_SERVICE="knowledgeforge-api-${ENVIRONMENT}"
WORKER_SERVICE="knowledgeforge-worker-${ENVIRONMENT}"
EXTRACTION_SERVICE="knowledgeforge-extraction-${ENVIRONMENT}"
OUTBOX_JOB="knowledgeforge-outbox-${ENVIRONMENT}"
SCHEDULER_JOB="knowledgeforge-outbox-scheduler-${ENVIRONMENT}"
BUCKET_NAME="${PROJECT_ID}-knowledgeforge-uploads"
REPO_NAME="knowledgeforge"

TOPIC="knowledgeforge-ingestion"
DEAD_LETTER_TOPIC="knowledgeforge-ingestion-dead-letter"
SUBSCRIPTION="knowledgeforge-ingestion-worker"
EXTRACTION_TOPIC="knowledgeforge-extraction"
EXTRACTION_DLQ_TOPIC="knowledgeforge-extraction-dead-letter"
EXTRACTION_SUBSCRIPTION="knowledgeforge-extraction-worker"

SECRET_DB="knowledgeforge-database-url"
SECRET_GEMINI="knowledgeforge-gemini-api-key"
SECRET_JWT="knowledgeforge-jwt-secret"

# Dedicated runtime identities (never the default compute account).
API_SA="knowledgeforge-api-runtime"
WORKER_SA="knowledgeforge-worker-runtime"
EXTRACTION_SA="knowledgeforge-extraction-runtime"
OUTBOX_SA="knowledgeforge-outbox-runtime"
SCHEDULER_SA="knowledgeforge-scheduler"
INGESTION_PUSH_SA="knowledgeforge-pubsub-push"
EXTRACTION_PUSH_SA="knowledgeforge-extraction-push"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=========================================================="
echo " KnowledgeForge AI: Cloud Run Deployment (Bash)"
echo "=========================================================="

if [ -z "$PROJECT_ID" ]; then
    echo "No Project ID provided."
    read -rp "Enter your Google Cloud Project ID: " PROJECT_ID
fi
if [ -z "$PROJECT_ID" ]; then
    echo "Error: Google Cloud Project ID is required."
    exit 1
fi
if [ -z "${DATABASE_URL:-}" ]; then
    echo "Error: DATABASE_URL is required (free pgvector hosts: Neon, Supabase)."
    exit 1
fi
if [ -z "${GEMINI_API_KEY:-}" ]; then
    echo "Error: GEMINI_API_KEY is required."
    exit 1
fi

echo "Project: $PROJECT_ID | Region: $REGION | Env: $ENVIRONMENT"

# 1. Preflight
echo ""
echo "[1/10] Running preflight checks..."
if ! command -v gcloud &> /dev/null; then
    echo "Error: gcloud CLI not found. Install Google Cloud SDK."
    exit 1
fi
gcloud config set project "$PROJECT_ID" --quiet
ACCOUNT=$(gcloud config get-value account 2>/dev/null || true)
if [ -z "$ACCOUNT" ]; then
    echo "Error: No authenticated gcloud account. Run 'gcloud auth login' first."
    exit 1
fi
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
if ! command -v uv &> /dev/null; then
    echo "Error: uv not found (https://docs.astral.sh/uv/). It applies the migrations."
    exit 1
fi
if ! command -v git &> /dev/null; then
    echo "Error: git not found; image tags derive from the commit SHA."
    exit 1
fi
IMAGE_TAG=$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)
echo "    OK: authenticated as $ACCOUNT; image tag: $IMAGE_TAG"

# 2. Enable APIs
echo ""
echo "[2/10] Enabling required Google Cloud APIs..."
gcloud services enable run.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    secretmanager.googleapis.com \
    pubsub.googleapis.com \
    storage.googleapis.com \
    iam.googleapis.com \
    cloudscheduler.googleapis.com --quiet
echo "    OK: APIs enabled"

# 3. Artifact Registry + images (one Cloud Build submission, commit-SHA tag)
echo ""
echo "[3/10] Building API and worker images via Cloud Build..."
if ! gcloud artifacts repositories describe "$REPO_NAME" --location="$REGION" &>/dev/null; then
    gcloud artifacts repositories create "$REPO_NAME" \
        --repository-format=docker \
        --location="$REGION" \
        --description="Docker images for KnowledgeForge AI" \
        --quiet
fi
REGISTRY="$REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME"
API_IMAGE="$REGISTRY/knowledgeforge-api"
WORKER_IMAGE="$REGISTRY/knowledgeforge-worker"
CLOUDBUILD_FILE="$(mktemp cloudbuild-kf-XXXXXX.yaml)"
trap 'rm -f "$CLOUDBUILD_FILE"' EXIT
cat > "$CLOUDBUILD_FILE" <<EOF
steps:
  - name: 'gcr.io/cloud-builders/docker'
    args: ['build', '-t', '$API_IMAGE:$IMAGE_TAG', '-t', '$API_IMAGE:latest', '-f', 'Dockerfile', '.']
  - name: 'gcr.io/cloud-builders/docker'
    args: ['build', '-t', '$WORKER_IMAGE:$IMAGE_TAG', '-t', '$WORKER_IMAGE:latest', '-f', 'infrastructure/Dockerfile.worker', '.']
images:
  - '$API_IMAGE:$IMAGE_TAG'
  - '$API_IMAGE:latest'
  - '$WORKER_IMAGE:$IMAGE_TAG'
  - '$WORKER_IMAGE:latest'
EOF
(cd "$REPO_ROOT" && gcloud builds submit --config "$CLOUDBUILD_FILE" --timeout="20m" --quiet)
echo "    OK: images built ($IMAGE_TAG)"

# 4. Secrets: create, verify, or explicitly rotate
echo ""
echo "[4/10] Ensuring Secret Manager secrets..."
ensure_secret() {
    local name="$1" value="$2"
    if gcloud secrets describe "$name" &>/dev/null; then
        local current
        current=$(gcloud secrets versions access latest --secret="$name" 2>/dev/null || true)
        if [ "$current" != "$value" ]; then
            if [ "$ROTATE_SECRETS" = "true" ]; then
                gcloud secrets versions add "$name" --data="$value" --quiet
                local count
                count=$(gcloud secrets versions list --secret="$name" \
                    --filter="state=ENABLED" --format="value(name)" | wc -l)
                if [ "$count" -gt 6 ]; then
                    echo "    Note: secret $name has $count ENABLED versions; the no-cost tier covers 6."
                fi
                echo "    OK: rotated $name (new version added)"
            else
                echo "Error: secret $name exists with a different value than the environment."
                echo "       Re-run with ROTATE_SECRETS=true to add a new version deliberately."
                echo "       Rotating JWT_SECRET_KEY invalidates active sessions; rotating"
                echo "       DATABASE_URL changes where services connect. Never print values."
                exit 1
            fi
        else
            echo "    OK: secret $name matches the environment"
        fi
    else
        gcloud secrets create "$name" --data="$value" --replication-policy="automatic" --quiet
        echo "    OK: secret $name created"
    fi
}
if [ -z "${JWT_SECRET_KEY:-}" ]; then
    JWT_SECRET_KEY="$(openssl rand -base64 48 | tr -d '\n')"
    echo "Generated a new JWT secret (reused from Secret Manager on later runs)."
fi
ensure_secret "$SECRET_DB" "$DATABASE_URL"
ensure_secret "$SECRET_GEMINI" "$GEMINI_API_KEY"
ensure_secret "$SECRET_JWT" "$JWT_SECRET_KEY"

# 5. Storage + Pub/Sub topics
echo ""
echo "[5/10] Creating GCS bucket and Pub/Sub topics..."
if ! gcloud storage buckets describe "gs://$BUCKET_NAME" &>/dev/null; then
    gcloud storage buckets create "gs://$BUCKET_NAME" --location="$REGION" --uniform-bucket-level-access
    echo "    OK: bucket created gs://$BUCKET_NAME"
else
    echo "    OK: bucket exists gs://$BUCKET_NAME"
fi
gcloud pubsub topics create "$TOPIC" --quiet >/dev/null 2>&1 || true
gcloud pubsub topics create "$DEAD_LETTER_TOPIC" --quiet >/dev/null 2>&1 || true
gcloud pubsub topics create "$EXTRACTION_TOPIC" --quiet >/dev/null 2>&1 || true
gcloud pubsub topics create "$EXTRACTION_DLQ_TOPIC" --quiet >/dev/null 2>&1 || true
# Pub/Sub's service agent publishes to the dead-letter topics on our behalf.
for DLQ in "$DEAD_LETTER_TOPIC" "$EXTRACTION_DLQ_TOPIC"; do
    gcloud pubsub topics add-iam-policy-binding "$DLQ" \
        --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com" \
        --role="roles/pubsub.publisher" --quiet >/dev/null 2>&1 || true
done
echo "    OK: topics ready ($TOPIC, $EXTRACTION_TOPIC + dead-letters)"

# 6. Service accounts + least-privilege IAM
echo ""
echo "[6/10] Configuring service accounts and IAM..."
for SA in "$API_SA" "$WORKER_SA" "$EXTRACTION_SA" "$OUTBOX_SA" "$SCHEDULER_SA" \
           "$INGESTION_PUSH_SA" "$EXTRACTION_PUSH_SA"; do
    if ! gcloud iam service-accounts describe "${SA}@${PROJECT_ID}.iam.gserviceaccount.com" &>/dev/null; then
        gcloud iam service-accounts create "$SA" --quiet
    fi
done
grant_secret() {
    gcloud secrets add-iam-policy-binding "$1" \
        --member="serviceAccount:$2@${PROJECT_ID}.iam.gserviceaccount.com" \
        --role="roles/secretmanager.secretAccessor" --quiet >/dev/null 2>&1 || true
}
grant_secret "$SECRET_DB" "$API_SA"; grant_secret "$SECRET_GEMINI" "$API_SA"; grant_secret "$SECRET_JWT" "$API_SA"
grant_secret "$SECRET_DB" "$WORKER_SA"; grant_secret "$SECRET_GEMINI" "$WORKER_SA"
grant_secret "$SECRET_DB" "$EXTRACTION_SA"; grant_secret "$SECRET_GEMINI" "$EXTRACTION_SA"
grant_secret "$SECRET_DB" "$OUTBOX_SA"
for RUNTIME_SA in "$WORKER_SA" "$EXTRACTION_SA"; do
    gcloud storage buckets add-iam-policy-binding "gs://$BUCKET_NAME" \
        --member="serviceAccount:${RUNTIME_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
        --role="roles/storage.objectAdmin" --quiet >/dev/null 2>&1 || true
done
# Pub/Sub's service agent mints OIDC tokens as the push identities.
for PUSH_SA in "$INGESTION_PUSH_SA" "$EXTRACTION_PUSH_SA"; do
    gcloud iam service-accounts add-iam-policy-binding \
        "${PUSH_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
        --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com" \
        --role="roles/iam.serviceAccountTokenCreator" --quiet >/dev/null 2>&1 || true
done
# The scheduler identity executes the outbox Cloud Run Job.
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SCHEDULER_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/run.invoker" --quiet >/dev/null 2>&1 || true
echo "    OK: service accounts and IAM bindings ready"

# 7. Workers, extraction service, outbox Job, subscriptions, scheduler
echo ""
echo "[7/10] Deploying ingestion worker, extraction worker, and outbox dispatcher..."
INGESTION_PUSH_EMAIL="${INGESTION_PUSH_SA}@${PROJECT_ID}.iam.gserviceaccount.com"
EXTRACTION_PUSH_EMAIL="${EXTRACTION_PUSH_SA}@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud run deploy "$WORKER_SERVICE" \
    --image "$WORKER_IMAGE:$IMAGE_TAG" \
    --region "$REGION" \
    --no-allow-unauthenticated \
    --port 8080 \
    --memory 1Gi --cpu 1 \
    --min-instances 0 --max-instances 3 \
    --service-account "${WORKER_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
    --set-env-vars "GCP_PROJECT_ID=$PROJECT_ID,GCS_BUCKET=$BUCKET_NAME,PUBSUB_SUBSCRIPTION=$SUBSCRIPTION,WORKER_OIDC_AUDIENCE=https://$WORKER_SERVICE-$PROJECT_NUMBER.$REGION.run.app" \
    --set-secrets "DATABASE_URL=$SECRET_DB:latest,GEMINI_API_KEY=$SECRET_GEMINI:latest" \
    --quiet
gcloud run services add-iam-policy-binding "$WORKER_SERVICE" \
    --region "$REGION" \
    --member="serviceAccount:$INGESTION_PUSH_EMAIL" \
    --role="roles/run.invoker" --quiet >/dev/null 2>&1 || true
WORKER_URL=$(gcloud run services describe "$WORKER_SERVICE" --region "$REGION" --format="value(status.url)")
if gcloud pubsub subscriptions describe "$SUBSCRIPTION" &>/dev/null; then
    gcloud pubsub subscriptions modify-push-config "$SUBSCRIPTION" \
        --push-endpoint="$WORKER_URL/" \
        --push-auth-service-account="$INGESTION_PUSH_EMAIL" --quiet
else
    gcloud pubsub subscriptions create "$SUBSCRIPTION" \
        --topic="$TOPIC" \
        --push-endpoint="$WORKER_URL/" \
        --push-auth-service-account="$INGESTION_PUSH_EMAIL" \
        --dead-letter-topic="$DEAD_LETTER_TOPIC" \
        --max-delivery-attempts=5 --quiet
fi
echo "    OK: ingestion worker deployed (private; Pub/Sub push with OIDC)"

gcloud run deploy "$EXTRACTION_SERVICE" \
    --image "$WORKER_IMAGE:$IMAGE_TAG" \
    --region "$REGION" \
    --no-allow-unauthenticated \
    --port 8080 \
    --memory 1Gi --cpu 1 \
    --min-instances 0 --max-instances 3 \
    --service-account "${EXTRACTION_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
    --command python \
    --args "-m,uvicorn,knowledgeforge.worker.extraction_entrypoint:app,--host,0.0.0.0,--port,8080" \
    --set-env-vars "GCP_PROJECT_ID=$PROJECT_ID,GCS_BUCKET=$BUCKET_NAME,EXTRACTION_SUBSCRIPTION=$EXTRACTION_SUBSCRIPTION,ENVIRONMENT=production" \
    --set-secrets "DATABASE_URL=$SECRET_DB:latest,GEMINI_API_KEY=$SECRET_GEMINI:latest" \
    --quiet
gcloud run services add-iam-policy-binding "$EXTRACTION_SERVICE" \
    --region "$REGION" \
    --member="serviceAccount:$EXTRACTION_PUSH_EMAIL" \
    --role="roles/run.invoker" --quiet >/dev/null 2>&1 || true
EXTRACTION_URL=$(gcloud run services describe "$EXTRACTION_SERVICE" --region "$REGION" --format="value(status.url)")
# In-app OIDC verification: the audience is the service's own URL.
gcloud run services update "$EXTRACTION_SERVICE" \
    --region "$REGION" \
    --update-env-vars "EXTRACTION_WORKER_OIDC_AUDIENCE=$EXTRACTION_URL" --quiet
if gcloud pubsub subscriptions describe "$EXTRACTION_SUBSCRIPTION" &>/dev/null; then
    gcloud pubsub subscriptions modify-push-config "$EXTRACTION_SUBSCRIPTION" \
        --push-endpoint="$EXTRACTION_URL/" \
        --push-auth-service-account="$EXTRACTION_PUSH_EMAIL" --quiet
else
    gcloud pubsub subscriptions create "$EXTRACTION_SUBSCRIPTION" \
        --topic="$EXTRACTION_TOPIC" \
        --push-endpoint="$EXTRACTION_URL/" \
        --push-auth-service-account="$EXTRACTION_PUSH_EMAIL" \
        --dead-letter-topic="$EXTRACTION_DLQ_TOPIC" \
        --max-delivery-attempts=5 --quiet
fi
echo "    OK: extraction worker deployed (private; Pub/Sub push with OIDC)"

# Bounded outbox dispatcher: a Cloud Run Job invoked by Cloud Scheduler, so
# no always-on poller is needed (scale-to-zero stays honest).
if gcloud run jobs describe "$OUTBOX_JOB" --region "$REGION" &>/dev/null; then
    gcloud run jobs update "$OUTBOX_JOB" \
        --region "$REGION" \
        --image "$WORKER_IMAGE:$IMAGE_TAG" \
        --memory 512Mi --cpu 1 \
        --service-account "${OUTBOX_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
        --set-env-vars "GCP_PROJECT_ID=$PROJECT_ID,EXTRACTION_TOPIC=$EXTRACTION_TOPIC,ENVIRONMENT=production" \
        --set-secrets "DATABASE_URL=$SECRET_DB:latest" \
        --command python \
        --args "-m,knowledgeforge.extraction.outbox_dispatcher" \
        --quiet
else
    gcloud run jobs create "$OUTBOX_JOB" \
        --region "$REGION" \
        --image "$WORKER_IMAGE:$IMAGE_TAG" \
        --memory 512Mi --cpu 1 \
        --service-account "${OUTBOX_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
        --set-env-vars "GCP_PROJECT_ID=$PROJECT_ID,EXTRACTION_TOPIC=$EXTRACTION_TOPIC,ENVIRONMENT=production" \
        --set-secrets "DATABASE_URL=$SECRET_DB:latest" \
        --command python \
        --args "-m,knowledgeforge.extraction.outbox_dispatcher" \
        --quiet
fi
gcloud run jobs update-iam "$OUTBOX_JOB" \
    --region "$REGION" \
    --member="serviceAccount:${SCHEDULER_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
    --role="roles/run.invoker" --quiet >/dev/null 2>&1 || true
if gcloud scheduler jobs describe "$SCHEDULER_JOB" --location "$REGION" &>/dev/null; then
    gcloud scheduler jobs update http "$SCHEDULER_JOB" \
        --location "$REGION" \
        --schedule="*/2 * * * *" \
        --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${OUTBOX_JOB}:run" \
        --oauth-service-account-email="${SCHEDULER_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
        --quiet
else
    gcloud scheduler jobs create http "$SCHEDULER_JOB" \
        --location "$REGION" \
        --schedule="*/2 * * * *" \
        --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${OUTBOX_JOB}:run" \
        --oauth-service-account-email="${SCHEDULER_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
        --quiet
fi
echo "    OK: outbox dispatcher job + scheduler trigger deployed"

# 8. Migrations
echo ""
echo "[8/10] Applying database migrations..."
(cd "$REPO_ROOT" && DATABASE_URL="$DATABASE_URL" uv run python scripts/apply_migrations.py)
echo "    OK: migrations applied"

# 9. API
echo ""
echo "[9/10] Deploying API service..."
EXTRA_ENV="ASYNC_INGESTION=true,ENVIRONMENT=production,GCP_PROJECT_ID=$PROJECT_ID,GCS_BUCKET=$BUCKET_NAME,PUBSUB_TOPIC=$TOPIC,PUBSUB_SUBSCRIPTION=$SUBSCRIPTION"
if [ -n "${REDIS_URL:-}" ]; then
    EXTRA_ENV="$EXTRA_ENV,REDIS_URL=$REDIS_URL"
fi
gcloud run deploy "$API_SERVICE" \
    --image "$API_IMAGE:$IMAGE_TAG" \
    --region "$REGION" \
    --allow-unauthenticated \
    --port 8000 \
    --memory 1Gi --cpu 1 \
    --min-instances 0 --max-instances 10 \
    --service-account "${API_SA}@${PROJECT_ID}.iam.gserviceaccount.com" \
    --set-env-vars "$EXTRA_ENV" \
    --set-secrets "DATABASE_URL=$SECRET_DB:latest,GEMINI_API_KEY=$SECRET_GEMINI:latest,JWT_SECRET_KEY=$SECRET_JWT:latest" \
    --quiet
API_URL=$(gcloud run services describe "$API_SERVICE" --region "$REGION" --format="value(status.url)")
echo "    OK: API deployed at $API_URL"

# 10. Verification
echo ""
echo "[10/10] Running health and functional smoke test..."
if ! curl -fsS "${API_URL}/health" >/dev/null; then
    echo "Error: $API_URL/health did not return 200."
    exit 1
fi
echo "    OK: health check passed"
if ! (cd "$REPO_ROOT" && API_BASE_URL="$API_URL" uv run python scripts/deploy_smoke_test.py); then
    echo "Error: functional smoke test failed against $API_URL"
    exit 1
fi
echo "    OK: functional smoke test passed"

echo ""
echo "=========================================================="
echo " Deployment Successful!"
echo " API URL:       $API_URL"
echo " Ingestion:     $WORKER_URL (private)"
echo " Extraction:    $EXTRACTION_URL (private)"
echo " Outbox:        job $OUTBOX_JOB <- scheduler $SCHEDULER_JOB (every 2 min)"
echo " Bucket:        gs://$BUCKET_NAME"
echo " Image tag:     $IMAGE_TAG"
echo " Topics:        $TOPIC -> $SUBSCRIPTION -> $WORKER_SERVICE"
echo "                $EXTRACTION_TOPIC -> $EXTRACTION_SUBSCRIPTION -> $EXTRACTION_SERVICE"
echo "=========================================================="
