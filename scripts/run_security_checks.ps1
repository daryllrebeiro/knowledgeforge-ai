$ErrorActionPreference = "Stop"

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    throw "Python is required to run dependency checks."
}

& $python.Source -m pip install --disable-pip-version-check pip-audit
& $python.Source -m pip_audit --project .

if (Get-Command trivy -ErrorAction SilentlyContinue) {
    trivy fs --scanners vuln,secret,misconfig .
} else {
    Write-Warning "Trivy is not installed locally. The GitHub Actions security workflow still runs it."
}
