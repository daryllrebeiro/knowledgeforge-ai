# =============================================================================
# KnowledgeForge AI: R6 local execution evidence runner (PowerShell)
#
# Runs every locally-runnable R6 checklist item and records command output
# under docs/evidence/<timestamp>/ for docs/validation-status.md:
#   quality-gates      ruff check + format, mypy src
#   unit-tests         pytest (no integration/live) + coverage ratchet
#   integration-tests  real-PostgreSQL suite (only when DATABASE_URL is set)
#   emulator-smoke     full local stack: register -> upload -> worker -> ask
#   load-run           Locust /ask load profile (only when R6_LOAD_RUN=1)
#   chaos-drills       Redis loss + Pub/Sub redelivery storm (F6.3)
#   backup-restore     pg_dump/pg_restore integrity (only when
#                      RESTORE_DATABASE_URL is set)
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts/r6_evidence.ps1
#   $env:DATABASE_URL = "postgresql://..."; $env:RESTORE_DATABASE_URL = "..."
#   $env:R6_LOAD_RUN = "1"; powershell -File scripts/r6_evidence.ps1
#
# Requirements: uv, Docker (for the emulator/chaos/load steps), pg_dump +
# pg_restore (for backup-restore).
# =============================================================================

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location (Split-Path -Parent $scriptDir)

$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss") + "Z"
$evidenceDir = "docs/evidence/$stamp"
New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
$summaryPath = Join-Path $evidenceDir "summary.txt"
$script:Results = @()
$full = "docker-compose.full.yml"
$chaos = "docker-compose.chaos.yml"

# Docker Desktop is often not on PATH in fresh shells.
$dockerCommand = (Get-Command docker -ErrorAction SilentlyContinue).Source
if (-not $dockerCommand) {
    $knownPath = "C:\Users\Lenovo Laptop\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe"
    if (Test-Path -LiteralPath $knownPath) { $dockerCommand = $knownPath }
}

function Invoke-Step {
    param([string]$Name, [scriptblock]$Body)
    Write-Host ""
    Write-Host "=== $Name ===" -ForegroundColor Cyan
    $log = Join-Path $evidenceDir "$Name.log"
    # Native stderr must not abort mid-step; exit codes decide (checked in the
    # step bodies and again here as a backstop). Reset LASTEXITCODE so a step
    # that runs no native command (a skip) is not judged by the previous step.
    $global:LASTEXITCODE = 0
    $ErrorActionPreference = "Continue"
    try {
        & $Body 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $log
        if ($LASTEXITCODE -ne 0) { throw "$Name exited with code $LASTEXITCODE" }
        $script:Results += "PASS  $Name"
        Write-Host "PASS  $Name" -ForegroundColor Green
    } catch {
        $script:Results += "FAIL  $Name (see $log)"
        Write-Host "FAIL  $Name (see $log)" -ForegroundColor Red
    } finally {
        $ErrorActionPreference = "Stop"
    }
}

function Save-StackLogs {
    param([string]$Name, [string[]]$ComposeArgs)
    & $dockerCommand compose @ComposeArgs logs --no-color |
        Out-File -FilePath (Join-Path $evidenceDir "$Name-stack.log")
}

function Step-QualityGates {
    uv run ruff check .
    if ($LASTEXITCODE -ne 0) { throw "ruff check failed" }
    uv run ruff format --check .
    if ($LASTEXITCODE -ne 0) { throw "ruff format check failed" }
    uv run mypy src
    if ($LASTEXITCODE -ne 0) { throw "mypy failed" }
}

function Step-UnitTests {
    uv run pytest -m "not integration and not live" --cov=knowledgeforge `
        --cov-report=term --cov-report=json:coverage.json
    if ($LASTEXITCODE -ne 0) { throw "unit tests failed" }
    uv run python scripts/coverage_ratchet.py coverage.json
    if ($LASTEXITCODE -ne 0) { throw "coverage ratchet failed" }
}

function Step-IntegrationTests {
    if (-not $env:DATABASE_URL) {
        Write-Output "SKIPPED: set DATABASE_URL to a local PostgreSQL to run the real-DB suite."
        return
    }
    uv run python scripts/apply_migrations.py
    if ($LASTEXITCODE -ne 0) { throw "migrations failed" }
    uv run pytest -m integration
    if ($LASTEXITCODE -ne 0) { throw "integration tests failed" }
}

function Step-EmulatorSmoke {
    & $dockerCommand compose -f $full up -d --build --scale smoke=0
    if ($LASTEXITCODE -ne 0) { throw "stack did not start" }
    & $dockerCommand compose -f $full run --rm smoke
    $smokeExit = $LASTEXITCODE
    Save-StackLogs "emulator-smoke" @("-f", $full)
    if ($env:R6_LOAD_RUN -eq "1") {
        Write-Output "Stack left running for the load run (R6_LOAD_RUN=1)."
    } else {
        & $dockerCommand compose -f $full down --volumes --remove-orphans
    }
    if ($smokeExit -ne 0) { throw "smoke test failed" }
}

function Step-LoadRun {
    if ($env:R6_LOAD_RUN -ne "1") {
        Write-Output "SKIPPED: set R6_LOAD_RUN=1 to include a headless Locust run"
        Write-Output "(10 users, 2/second spawn, 1 minute) against the local stack."
        return
    }
    uv run --with locust locust -f scripts/locustfile.py --host http://localhost:8000 `
        --headless -u 10 -r 2 -t 1m --csv (Join-Path $evidenceDir "load")
    $locustExit = $LASTEXITCODE
    & $dockerCommand compose -f $full down --volumes --remove-orphans
    if ($locustExit -ne 0) { throw "locust run failed" }
}

function Step-ChaosDrills {
    & $dockerCommand compose -f $full -f $chaos up -d --build --scale smoke=0
    if ($LASTEXITCODE -ne 0) { throw "chaos stack did not start" }
    & $dockerCommand compose -f $full -f $chaos run --rm chaos
    $chaosExit = $LASTEXITCODE
    Save-StackLogs "chaos-drills" @("-f", $full, "-f", $chaos)
    & $dockerCommand compose -f $full -f $chaos down --volumes --remove-orphans
    if ($chaosExit -ne 0) { throw "chaos drills failed" }
}

function Step-BackupRestore {
    if (-not $env:RESTORE_DATABASE_URL) {
        Write-Output "SKIPPED: set RESTORE_DATABASE_URL (and DATABASE_URL) to run the"
        Write-Output "backup/restore integrity check; pg_dump and pg_restore must be on PATH."
        return
    }
    uv run python scripts/backup_restore_check.py
    if ($LASTEXITCODE -ne 0) { throw "backup/restore check failed" }
}

"KnowledgeForge R6 evidence run" | Out-File -FilePath $summaryPath
"Started: $((Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'))" |
    Out-File -FilePath $summaryPath -Append

# emulator-smoke must precede load-run (which reuses its still-running stack)
# and chaos-drills (which tears the stack down for its own override).
Invoke-Step "quality-gates" { Step-QualityGates }
Invoke-Step "unit-tests" { Step-UnitTests }
Invoke-Step "integration-tests" { Step-IntegrationTests }
if (-not $dockerCommand) {
    Write-Host "FAIL  emulator/chaos/load steps (Docker not found)" -ForegroundColor Red
    $script:Results += "FAIL  emulator-smoke (Docker not found)"
    $script:Results += "FAIL  load-run (Docker not found)"
    $script:Results += "FAIL  chaos-drills (Docker not found)"
} else {
    Invoke-Step "emulator-smoke" { Step-EmulatorSmoke }
    Invoke-Step "load-run" { Step-LoadRun }
    Invoke-Step "chaos-drills" { Step-ChaosDrills }
}
Invoke-Step "backup-restore" { Step-BackupRestore }

""
$script:Results | Tee-Object -FilePath $summaryPath -Append
""
Write-Host "Evidence written to $evidenceDir (paste the relevant numbers into docs/validation-status.md)."

if ($script:Results -match "^FAIL") { exit 1 }
exit 0
