# =============================================================================
# KnowledgeForge AI: One-Command Google Cloud Run Deployment (PowerShell)
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
# Idempotent — safe to re-run.
# =============================================================================

param(
    [Parameter(Mandatory = $false)][string]$ProjectId = $env:GOOGLE_CLOUD_PROJECT,
    [string]$Region = $(if ($env:GOOGLE_CLOUD_REGION) { $env:GOOGLE_CLOUD_REGION } else { "us-central1" }),
    [string]$Environment = $(if ($env:KNOWLEDGEFORGE_ENVIRONMENT) { $env:KNOWLEDGEFORGE_ENVIRONMENT } else { "staging" })
)

$ErrorActionPreference = "Stop"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " KnowledgeForge AI: Cloud Run Deployment (PowerShell)" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$RotateSecrets = ($env:ROTATE_SECRETS -eq "true")

$ApiService = "knowledgeforge-api-$Environment"
$WorkerService = "knowledgeforge-worker-$Environment"
$ExtractionService = "knowledgeforge-extraction-$Environment"
$OutboxJob = "knowledgeforge-outbox-$Environment"
$SchedulerJob = "knowledgeforge-outbox-scheduler-$Environment"
$RepoName = "knowledgeforge"
$BucketName = "$ProjectId-knowledgeforge-uploads"

$Topic = "knowledgeforge-ingestion"
$DeadLetterTopic = "knowledgeforge-ingestion-dead-letter"
$Subscription = "knowledgeforge-ingestion-worker"
$ExtractionTopic = "knowledgeforge-extraction"
$ExtractionDlqTopic = "knowledgeforge-extraction-dead-letter"
$ExtractionSubscription = "knowledgeforge-extraction-worker"

$SecretDb = "knowledgeforge-database-url"
$SecretGemini = "knowledgeforge-gemini-api-key"
$SecretJwt = "knowledgeforge-jwt-secret"

# Dedicated runtime identities (never the default compute account).
$ApiSa = "knowledgeforge-api-runtime"
$WorkerSa = "knowledgeforge-worker-runtime"
$ExtractionSa = "knowledgeforge-extraction-runtime"
$OutboxSa = "knowledgeforge-outbox-runtime"
$SchedulerSa = "knowledgeforge-scheduler"
$IngestionPushSa = "knowledgeforge-pubsub-push"
$ExtractionPushSa = "knowledgeforge-extraction-push"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir

# 0. Validate inputs
if (-not $ProjectId) {
    Write-Host ""
    $ProjectId = Read-Host "Enter your Google Cloud Project ID"
}
if (-not $ProjectId) {
    Write-Error "Google Cloud Project ID is required."
    exit 1
}

if (-not $env:DATABASE_URL) {
    Write-Error "DATABASE_URL is required (free pgvector hosts: Neon, Supabase)."
    exit 1
}
if (-not $env:GEMINI_API_KEY) {
    Write-Error "GEMINI_API_KEY is required."
    exit 1
}
if (-not $env:JWT_SECRET_KEY) {
    $env:JWT_SECRET_KEY = [Convert]::ToBase64String(
        [System.Security.Cryptography.RandomNumberGenerator]::GetBytes(48))
    Write-Host "Generated a new JWT secret (reused from Secret Manager on later runs)."
}

Write-Host "Project: $ProjectId | Region: $Region | Env: $Environment" -ForegroundColor White

# 1. Preflight
Write-Host ""
Write-Host "[1/10] Running preflight checks..." -ForegroundColor Yellow
if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
    Write-Error "gcloud CLI not found. Install Google Cloud SDK: https://cloud.google.com/sdk/docs/install"
    exit 1
}
& gcloud config set project $ProjectId --quiet
$account = & gcloud config get-value account 2>$null
if (-not $account) {
    Write-Error "No authenticated gcloud account found. Run 'gcloud auth login' first."
    exit 1
}
$projectNumber = (& gcloud projects describe $ProjectId --format="value(projectNumber)").Trim()
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Error "uv not found (https://docs.astral.sh/uv/). It applies the migrations."
    exit 1
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Error "git not found; image tags derive from the commit SHA."
    exit 1
}
try {
    $imageTag = (& git -C $repoRoot rev-parse --short HEAD).Trim()
} catch {
    $imageTag = Get-Date -Format "yyyyMMddHHmmss"
}
Write-Host "    [OK] Authenticated as: $account | image tag: $imageTag" -ForegroundColor Green

# 2. Enable required GCP APIs
Write-Host ""
Write-Host "[2/10] Enabling required Google Cloud APIs..." -ForegroundColor Yellow
& gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com pubsub.googleapis.com storage.googleapis.com iam.googleapis.com cloudscheduler.googleapis.com --quiet
if ($LASTEXITCODE -ne 0) { Write-Error "API enablement failed."; exit 1 }
Write-Host "    [OK] APIs enabled" -ForegroundColor Green

# 3. Artifact Registry + images (one Cloud Build submission, commit-SHA tag)
Write-Host ""
Write-Host "[3/10] Building API and worker images via Cloud Build..." -ForegroundColor Yellow
$existingRepos = & gcloud artifacts repositories list --location=$Region --format="value(name)" 2>$null
$found = $false
foreach ($r in $existingRepos) { if ("$r" -match $RepoName) { $found = $true; break } }
if (-not $found) {
    & gcloud artifacts repositories create $RepoName --repository-format=docker --location=$Region --description="Docker images for KnowledgeForge AI" --quiet
    if ($LASTEXITCODE -ne 0) { Write-Error "Artifact Registry creation failed."; exit 1 }
}
$registry = "$Region-docker.pkg.dev/$ProjectId/$RepoName"
$apiImage = "$registry/knowledgeforge-api"
$workerImage = "$registry/knowledgeforge-worker"
$cloudBuildFile = Join-Path $env:TEMP "cloudbuild-kf.yaml"
@"
steps:
  - name: 'gcr.io/cloud-builders/docker'
    args: ['build', '-t', '$apiImage`:$imageTag', '-t', '$apiImage`:latest', '-f', 'Dockerfile', '.']
  - name: 'gcr.io/cloud-builders/docker'
    args: ['build', '-t', '$workerImage`:$imageTag', '-t', '$workerImage`:latest', '-f', 'infrastructure/Dockerfile.worker', '.']
images:
  - '$apiImage`:$imageTag'
  - '$apiImage`:latest'
  - '$workerImage`:$imageTag'
  - '$workerImage`:latest'
"@ | Set-Content -Path $cloudBuildFile -Encoding ascii

Push-Location $repoRoot
& gcloud builds submit --config $cloudBuildFile --timeout="20m" --quiet
if ($LASTEXITCODE -ne 0) { Pop-Location; Write-Error "Cloud Build failed."; exit 1 }
Pop-Location
Write-Host "    [OK] Images built ($imageTag)" -ForegroundColor Green

# 4. Secrets: create, verify, or explicitly rotate
Write-Host ""
Write-Host "[4/10] Ensuring Secret Manager secrets..." -ForegroundColor Yellow
function Ensure-Secret {
    param([string]$Name, [string]$Value)
    $existing = & gcloud secrets describe $Name 2>$null
    if ($LASTEXITCODE -eq 0 -and $existing) {
        # Value comparison without printing the secret.
        $tempFile = New-TemporaryFile
        try {
            $Value | Set-Content -Path $tempFile -NoNewline -Encoding utf8
            & gcloud secrets versions add $Name --data-file=$tempFile --quiet --dry-run 2>$null
            # gcloud has no diff command; compare via a version add attempt is
            # destructive, so compare hashes instead.
            $envHash = [System.BitConverter]::ToString(
                [System.Security.Cryptography.SHA256]::Create().ComputeHash(
                    [System.Text.Encoding]::UTF8.GetBytes($Value))).Replace("-", "").ToLower()
            $current = & gcloud secrets versions access latest --secret=$Name 2>$null
            $currentHash = [System.BitConverter]::ToString(
                [System.Security.Cryptography.SHA256]::Create().ComputeHash(
                    [System.Text.Encoding]::UTF8.GetBytes([string]$current))).Replace("-", "").ToLower()
            if ($envHash -ne $currentHash) {
                if ($RotateSecrets) {
                    $Value | gcloud secrets versions add $Name --data-file=- --quiet | Out-Null
                    $versions = (& gcloud secrets versions list --secret=$Name --filter="state=ENABLED" --format="value(name)" | Measure-Object).Count
                    if ($versions -gt 6) {
                        Write-Host "    Note: $Name has $versions ENABLED versions; the no-cost tier covers 6." -ForegroundColor DarkYellow
                    }
                    Write-Host "    [OK] Rotated $Name (new version added)" -ForegroundColor Green
                } else {
                    Write-Error "Secret $Name exists with a different value than the environment. Re-run with ROTATE_SECRETS=true to rotate deliberately (JWT rotation invalidates sessions; DATABASE_URL rotation changes the connection target)."
                    exit 1
                }
            } else {
                Write-Host "    [OK] Secret matches the environment: $Name" -ForegroundColor Green
            }
        } finally {
            Remove-Item $tempFile -ErrorAction SilentlyContinue
        }
    } else {
        $tempFile = New-TemporaryFile
        try {
            $Value | Set-Content -Path $tempFile -NoNewline -Encoding utf8
            & gcloud secrets create $Name --data-file=$tempFile --replication-policy="automatic" --quiet
            if ($LASTEXITCODE -ne 0) { Write-Error "Failed to create secret $Name."; exit 1 }
        } finally {
            Remove-Item $tempFile -ErrorAction SilentlyContinue
        }
        Write-Host "    [OK] Secret created: $Name" -ForegroundColor Green
    }
}
Ensure-Secret -Name $SecretDb -Value $env:DATABASE_URL
Ensure-Secret -Name $SecretGemini -Value $env:GEMINI_API_KEY
Ensure-Secret -Name $SecretJwt -Value $env:JWT_SECRET_KEY

# 5. GCS bucket + Pub/Sub topics
Write-Host ""
Write-Host "[5/10] Creating GCS bucket and Pub/Sub topics..." -ForegroundColor Yellow
$bucketExists = & gcloud storage buckets describe "gs://$BucketName" 2>$null
if ($LASTEXITCODE -ne 0 -or -not $bucketExists) {
    & gcloud storage buckets create "gs://$BucketName" --location=$Region --uniform-bucket-level-access
    if ($LASTEXITCODE -ne 0) { Write-Error "Bucket creation failed."; exit 1 }
    Write-Host "    [OK] Bucket created: gs://$BucketName" -ForegroundColor Green
} else {
    Write-Host "    [OK] Bucket exists: gs://$BucketName" -ForegroundColor Green
}
foreach ($t in @($Topic, $DeadLetterTopic, $ExtractionTopic, $ExtractionDlqTopic)) {
    & gcloud pubsub topics create $t --quiet 2>$null | Out-Null
}
# Pub/Sub's service agent publishes to the dead-letter topics on our behalf.
foreach ($dlq in @($DeadLetterTopic, $ExtractionDlqTopic)) {
    & gcloud pubsub topics add-iam-policy-binding $dlq --member="serviceAccount:service-$projectNumber@gcp-sa-pubsub.iam.gserviceaccount.com" --role="roles/pubsub.publisher" --quiet 2>$null | Out-Null
}
Write-Host "    [OK] Topics ready ($Topic, $ExtractionTopic + dead-letters)" -ForegroundColor Green

# 6. Service accounts + least-privilege IAM
Write-Host ""
Write-Host "[6/10] Configuring service accounts and IAM..." -ForegroundColor Yellow
foreach ($sa in @($ApiSa, $WorkerSa, $ExtractionSa, $OutboxSa, $SchedulerSa, $IngestionPushSa, $ExtractionPushSa)) {
    $saEmail = "$sa@$ProjectId.iam.gserviceaccount.com"
    $existingSa = & gcloud iam service-accounts describe $saEmail 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $existingSa) {
        & gcloud iam service-accounts create $sa --description="KnowledgeForge runtime identity ($sa)" --quiet
        if ($LASTEXITCODE -ne 0) { Write-Error "Service account creation failed: $sa"; exit 1 }
    }
}
function Grant-Secret {
    param([string]$Secret, [string]$Sa)
    & gcloud secrets add-iam-policy-binding $Secret --member="serviceAccount:$Sa@$ProjectId.iam.gserviceaccount.com" --role="roles/secretmanager.secretAccessor" --quiet 2>$null | Out-Null
}
Grant-Secret $SecretDb $ApiSa; Grant-Secret $SecretGemini $ApiSa; Grant-Secret $SecretJwt $ApiSa
Grant-Secret $SecretDb $WorkerSa; Grant-Secret $SecretGemini $WorkerSa
Grant-Secret $SecretDb $ExtractionSa; Grant-Secret $SecretGemini $ExtractionSa
Grant-Secret $SecretDb $OutboxSa
foreach ($runtimeSa in @($WorkerSa, $ExtractionSa)) {
    & gcloud storage buckets add-iam-policy-binding "gs://$BucketName" --member="serviceAccount:$runtimeSa@$ProjectId.iam.gserviceaccount.com" --role="roles/storage.objectAdmin" --quiet 2>$null | Out-Null
}
# Pub/Sub's service agent mints OIDC tokens as the push identities.
foreach ($pushSa in @($IngestionPushSa, $ExtractionPushSa)) {
    & gcloud iam service-accounts add-iam-policy-binding "$pushSa@$ProjectId.iam.gserviceaccount.com" --member="serviceAccount:service-$projectNumber@gcp-sa-pubsub.iam.gserviceaccount.com" --role="roles/iam.serviceAccountTokenCreator" --quiet 2>$null | Out-Null
}
# The scheduler identity executes the outbox Cloud Run Job.
& gcloud projects add-iam-policy-binding $ProjectId --member="serviceAccount:$SchedulerSa@$ProjectId.iam.gserviceaccount.com" --role="roles/run.invoker" --quiet 2>$null | Out-Null
Write-Host "    [OK] Service accounts and IAM bindings ready" -ForegroundColor Green

# 7. Workers, extraction service, outbox Job, subscriptions, scheduler
Write-Host ""
Write-Host "[7/10] Deploying ingestion worker, extraction worker, and outbox dispatcher..." -ForegroundColor Yellow
$ingestionPushEmail = "$IngestionPushSa@$ProjectId.iam.gserviceaccount.com"
$extractionPushEmail = "$ExtractionPushSa@$ProjectId.iam.gserviceaccount.com"

& gcloud run deploy $WorkerService --image "$workerImage`:$imageTag" --region $Region --no-allow-unauthenticated --port 8080 --memory 1Gi --cpu 1 --min-instances 0 --max-instances 3 --service-account "$WorkerSa@$ProjectId.iam.gserviceaccount.com" --set-env-vars "GCP_PROJECT_ID=$ProjectId,GCS_BUCKET=$BucketName,PUBSUB_SUBSCRIPTION=$Subscription,WORKER_OIDC_AUDIENCE=https://$WorkerService-$projectNumber.$Region.run.app" --set-secrets "DATABASE_URL=$SecretDb`:`:latest,GEMINI_API_KEY=$SecretGemini`:`:latest" --quiet
if ($LASTEXITCODE -ne 0) { Write-Error "Worker deployment failed."; exit 1 }
& gcloud run services add-iam-policy-binding $WorkerService --region $Region --member="serviceAccount:$ingestionPushEmail" --role="roles/run.invoker" --quiet 2>$null | Out-Null
$workerUrl = (& gcloud run services describe $WorkerService --region $Region --format="value(status.url)").Trim()
$subExists = & gcloud pubsub subscriptions describe $Subscription 2>$null
if ($LASTEXITCODE -eq 0 -and $subExists) {
    & gcloud pubsub subscriptions modify-push-config $Subscription --push-endpoint="$workerUrl/" --push-auth-service-account=$ingestionPushEmail --quiet
} else {
    & gcloud pubsub subscriptions create $Subscription --topic=$Topic --push-endpoint="$workerUrl/" --push-auth-service-account=$ingestionPushEmail --dead-letter-topic=$DeadLetterTopic --max-delivery-attempts=5 --quiet
}
if ($LASTEXITCODE -ne 0) { Write-Error "Ingestion subscription wiring failed."; exit 1 }
Write-Host "    [OK] Ingestion worker deployed (private; Pub/Sub push with OIDC)" -ForegroundColor Green

& gcloud run deploy $ExtractionService --image "$workerImage`:$imageTag" --region $Region --no-allow-unauthenticated --port 8080 --memory 1Gi --cpu 1 --min-instances 0 --max-instances 3 --service-account "$ExtractionSa@$ProjectId.iam.gserviceaccount.com" --command python --args "-m,uvicorn,knowledgeforge.worker.extraction_entrypoint:app,--host,0.0.0.0,--port,8080" --set-env-vars "GCP_PROJECT_ID=$ProjectId,GCS_BUCKET=$BucketName,EXTRACTION_SUBSCRIPTION=$ExtractionSubscription,ENVIRONMENT=production" --set-secrets "DATABASE_URL=$SecretDb`:`:latest,GEMINI_API_KEY=$SecretGemini`:`:latest" --quiet
if ($LASTEXITCODE -ne 0) { Write-Error "Extraction deployment failed."; exit 1 }
& gcloud run services add-iam-policy-binding $ExtractionService --region $Region --member="serviceAccount:$extractionPushEmail" --role="roles/run.invoker" --quiet 2>$null | Out-Null
$extractionUrl = (& gcloud run services describe $ExtractionService --region $Region --format="value(status.url)").Trim()
# In-app OIDC verification: the audience is the service's own URL.
& gcloud run services update $ExtractionService --region $Region --update-env-vars "EXTRACTION_WORKER_OIDC_AUDIENCE=$extractionUrl" --quiet
if ($LASTEXITCODE -ne 0) { Write-Error "Extraction audience update failed."; exit 1 }
$subExists = & gcloud pubsub subscriptions describe $ExtractionSubscription 2>$null
if ($LASTEXITCODE -eq 0 -and $subExists) {
    & gcloud pubsub subscriptions modify-push-config $ExtractionSubscription --push-endpoint="$extractionUrl/" --push-auth-service-account=$extractionPushEmail --quiet
} else {
    & gcloud pubsub subscriptions create $ExtractionSubscription --topic=$ExtractionTopic --push-endpoint="$extractionUrl/" --push-auth-service-account=$extractionPushEmail --dead-letter-topic=$ExtractionDlqTopic --max-delivery-attempts=5 --quiet
}
if ($LASTEXITCODE -ne 0) { Write-Error "Extraction subscription wiring failed."; exit 1 }
Write-Host "    [OK] Extraction worker deployed (private; Pub/Sub push with OIDC)" -ForegroundColor Green

# Bounded outbox dispatcher: a Cloud Run Job invoked by Cloud Scheduler, so
# no always-on poller is needed (scale-to-zero stays honest).
$jobExists = & gcloud run jobs describe $OutboxJob --region $Region 2>$null
if ($LASTEXITCODE -eq 0 -and $jobExists) {
    & gcloud run jobs update $OutboxJob --region $Region --image "$workerImage`:$imageTag" --memory 512Mi --cpu 1 --service-account "$OutboxSa@$ProjectId.iam.gserviceaccount.com" --set-env-vars "GCP_PROJECT_ID=$ProjectId,EXTRACTION_TOPIC=$ExtractionTopic,ENVIRONMENT=production" --set-secrets "DATABASE_URL=$SecretDb`:`:latest" --command python --args "-m,knowledgeforge.extraction.outbox_dispatcher" --quiet
} else {
    & gcloud run jobs create $OutboxJob --region $Region --image "$workerImage`:$imageTag" --memory 512Mi --cpu 1 --service-account "$OutboxSa@$ProjectId.iam.gserviceaccount.com" --set-env-vars "GCP_PROJECT_ID=$ProjectId,EXTRACTION_TOPIC=$ExtractionTopic,ENVIRONMENT=production" --set-secrets "DATABASE_URL=$SecretDb`:`:latest" --command python --args "-m,knowledgeforge.extraction.outbox_dispatcher" --quiet
}
if ($LASTEXITCODE -ne 0) { Write-Error "Outbox job deployment failed."; exit 1 }
& gcloud run jobs update-iam $OutboxJob --region $Region --member="serviceAccount:$SchedulerSa@$ProjectId.iam.gserviceaccount.com" --role="roles/run.invoker" --quiet 2>$null | Out-Null
$jobUri = "https://$Region-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$ProjectId/jobs/$OutboxJob`:run"
$schedulerExists = & gcloud scheduler jobs describe $SchedulerJob --location $Region 2>$null
if ($LASTEXITCODE -eq 0 -and $schedulerExists) {
    & gcloud scheduler jobs update http $SchedulerJob --location $Region --schedule="*/2 * * * *" --uri=$jobUri --oauth-service-account-email="$SchedulerSa@$ProjectId.iam.gserviceaccount.com" --quiet
} else {
    & gcloud scheduler jobs create http $SchedulerJob --location $Region --schedule="*/2 * * * *" --uri=$jobUri --oauth-service-account-email="$SchedulerSa@$ProjectId.iam.gserviceaccount.com" --quiet
}
if ($LASTEXITCODE -ne 0) { Write-Error "Scheduler creation failed."; exit 1 }
Write-Host "    [OK] Outbox dispatcher job + scheduler trigger deployed" -ForegroundColor Green

# 8. Migrations
Write-Host ""
Write-Host "[8/10] Applying database migrations..." -ForegroundColor Yellow
Push-Location $repoRoot
$env:DATABASE_URL = $env:DATABASE_URL
& uv run python scripts/apply_migrations.py
if ($LASTEXITCODE -ne 0) { Pop-Location; Write-Error "Migrations failed."; exit 1 }
Pop-Location
Write-Host "    [OK] Migrations applied" -ForegroundColor Green

# 9. API
Write-Host ""
Write-Host "[9/10] Deploying API service..." -ForegroundColor Yellow
$extraEnv = "ASYNC_INGESTION=true,ENVIRONMENT=production,GCP_PROJECT_ID=$ProjectId,GCS_BUCKET=$BucketName,PUBSUB_TOPIC=$Topic,PUBSUB_SUBSCRIPTION=$Subscription"
if ($env:REDIS_URL) { $extraEnv = "$extraEnv,REDIS_URL=$($env:REDIS_URL)" }
& gcloud run deploy $ApiService --image "$apiImage`:$imageTag" --region $Region --allow-unauthenticated --port 8000 --memory 1Gi --cpu 1 --min-instances 0 --max-instances 10 --service-account "$ApiSa@$ProjectId.iam.gserviceaccount.com" --set-env-vars $extraEnv --set-secrets "DATABASE_URL=$SecretDb`:`:latest,GEMINI_API_KEY=$SecretGemini`:`:latest,JWT_SECRET_KEY=$SecretJwt`:`:latest" --quiet
if ($LASTEXITCODE -ne 0) { Write-Error "API deployment failed."; exit 1 }
$apiUrl = (& gcloud run services describe $ApiService --region $Region --format="value(status.url)").Trim()
Write-Host "    [OK] API deployed at $apiUrl" -ForegroundColor Green

# 10. Verification
Write-Host ""
Write-Host "[10/10] Running health and functional smoke test..." -ForegroundColor Yellow
try {
    $health = Invoke-RestMethod -Uri "$apiUrl/health" -Method Get -TimeoutSec 30
    if ($health.status -ne "ok") { throw "unexpected health payload" }
} catch {
    Write-Error "Health check failed against $apiUrl/health : $_"
    exit 1
}
Write-Host "    [OK] Health check passed" -ForegroundColor Green
$env:API_BASE_URL = $apiUrl
Push-Location $repoRoot
& uv run python scripts/deploy_smoke_test.py
$smokeExit = $LASTEXITCODE
Pop-Location
if ($smokeExit -ne 0) {
    Write-Error "Functional smoke test failed against $apiUrl"
    exit 1
}
Write-Host "    [OK] Functional smoke test passed" -ForegroundColor Green

Write-Host ""
Write-Host "==========================================================" -ForegroundColor Green
Write-Host " [SUCCESS] Deployment Completed!" -ForegroundColor Green
Write-Host " API URL:       $apiUrl" -ForegroundColor Cyan
Write-Host " Ingestion:     $workerUrl (private)" -ForegroundColor White
Write-Host " Extraction:    $extractionUrl (private)" -ForegroundColor White
Write-Host " Outbox:        job $OutboxJob <- scheduler $SchedulerJob (every 2 min)" -ForegroundColor White
Write-Host " Bucket:        gs://$BucketName" -ForegroundColor White
Write-Host " Image tag:     $imageTag" -ForegroundColor White
Write-Host " Topics:        $Topic -> $Subscription -> $WorkerService" -ForegroundColor White
Write-Host "                $ExtractionTopic -> $ExtractionSubscription -> $ExtractionService" -ForegroundColor White
Write-Host "==========================================================" -ForegroundColor Green
