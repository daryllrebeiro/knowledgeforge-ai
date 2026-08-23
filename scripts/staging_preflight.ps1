$ErrorActionPreference = "Stop"

if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
    throw "gcloud CLI is not installed. Install the Google Cloud CLI before continuing."
}

$projectId = $env:GCP_PROJECT_ID
if ([string]::IsNullOrWhiteSpace($projectId) -or $projectId -eq "replace-me") {
    throw "Set GCP_PROJECT_ID to a real project with billing enabled."
}

$accounts = gcloud auth list --filter="status:ACTIVE" --format="value(account)"
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($accounts)) {
    throw "No active gcloud account is available. Run gcloud auth login."
}

gcloud config set project $projectId
if ($LASTEXITCODE -ne 0) {
    throw "Unable to select project $projectId."
}

$requiredApis = @(
    "run.googleapis.com",
    "sqladmin.googleapis.com",
    "storage.googleapis.com",
    "pubsub.googleapis.com",
    "secretmanager.googleapis.com",
    "monitoring.googleapis.com"
)

foreach ($api in $requiredApis) {
    $enabled = gcloud services list --enabled --filter="config.name=$api" --format="value(config.name)"
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect enabled APIs for $api."
    }
    if ($enabled -ne $api) {
        Write-Warning "API is not enabled: $api"
    }
}

Write-Output "GCP staging preflight completed for project $projectId."
