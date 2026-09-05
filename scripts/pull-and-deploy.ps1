# =============================================================================
# KnowledgeForge AI: Pull Latest Code, Test & Deploy Pipeline (PowerShell)
# Modeled after the-visualizer/scripts/pull-and-deploy.ps1
# =============================================================================

param(
    [Parameter(Mandatory = $false)][string]$ProjectId = $env:GOOGLE_CLOUD_PROJECT,
    [string]$Region = $(if ($env:GOOGLE_CLOUD_REGION) { $env:GOOGLE_CLOUD_REGION } else { "us-central1" })
)

$ErrorActionPreference = "Stop"

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host " KnowledgeForge AI: Pull, Test & Deploy Pipeline" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir

# 1. Stash any uncommitted changes
$status = git status --porcelain
$hasChanges = [bool]($status -and $status.Trim().Length -gt 0)

if ($hasChanges) {
    Write-Host "`n[1/4] Stashing local changes..." -ForegroundColor Yellow
    git stash push -m "pull-and-deploy-auto-stash-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
} else {
    Write-Host "`n[1/4] Working tree clean, skipping stash." -ForegroundColor Gray
}

# 2. Pull latest main branch
Write-Host "`n[2/4] Pulling latest code from origin..." -ForegroundColor Yellow
git pull --rebase origin main
if ($LASTEXITCODE -ne 0) { Write-Error "git pull failed."; exit 1 }

# 3. Run the same checks CI runs (lint, types, unit suite)
Write-Host "`n[3/4] Running lint, type check, and unit tests..." -ForegroundColor Yellow
Set-Location $repoRoot
uv run ruff check .
if ($LASTEXITCODE -ne 0) { Write-Error "ruff check failed."; exit 1 }
uv run ruff format --check .
if ($LASTEXITCODE -ne 0) { Write-Error "ruff format check failed."; exit 1 }
uv run mypy src
if ($LASTEXITCODE -ne 0) { Write-Error "mypy failed."; exit 1 }
uv run pytest -m "not integration and not live"
if ($LASTEXITCODE -ne 0) { Write-Error "Unit tests failed."; exit 1 }

# 4. Trigger Deployment
Write-Host "`n[4/4] Executing Cloud Run deployment..." -ForegroundColor Yellow
$deployScript = Join-Path $scriptDir "deploy-cloudrun.ps1"

if ($ProjectId) {
    & $deployScript -ProjectId $ProjectId -Region $Region
} else {
    & $deployScript -Region $Region
}
if ($LASTEXITCODE -ne 0) { Write-Error "Deployment failed."; exit 1 }
