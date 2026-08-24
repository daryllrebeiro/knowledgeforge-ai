param(
    [switch]$KeepRunning
)

$ErrorActionPreference = "Stop"

$dockerCommand = (Get-Command docker -ErrorAction SilentlyContinue).Source
if (-not $dockerCommand) {
    $knownPath = "C:\Users\Lenovo Laptop\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe"
    if (Test-Path -LiteralPath $knownPath) {
        $dockerCommand = $knownPath
    }
}
if (-not $dockerCommand) {
    throw "Docker Desktop or another Docker-compatible runtime is required."
}

$composeFile = "docker-compose.full.yml"
& $dockerCommand compose -f $composeFile up -d --build --scale smoke=0
if ($LASTEXITCODE -ne 0) {
    throw "Unable to start the local emulator stack."
}

& $dockerCommand compose -f $composeFile run --rm smoke
if ($LASTEXITCODE -ne 0) {
    & $dockerCommand compose -f $composeFile logs
    throw "The local async smoke test failed."
}

Write-Output "Local async smoke test passed."
if (-not $KeepRunning) {
    Write-Output "Stack remains running for inspection. Run 'docker compose -f $composeFile down' when finished."
}
