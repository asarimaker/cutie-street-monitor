$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repositoryRoot

$venvPython = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Setup is required. Run setup-local.cmd first."
}

Write-Host "Checking the latest data on GitHub..." -ForegroundColor Cyan
git pull --ff-only origin main
if ($LASTEXITCODE -ne 0) {
    throw "Failed to retrieve the latest data from GitHub."
}

$env:MANUAL_MODE = "true"
$env:DAILY_MODE = "true"
try {
    & $venvPython monitor.py
    if ($LASTEXITCODE -ne 0) {
        throw "The monitor failed. If a diagnostics folder was created, check its contents."
    }
}
finally {
    Remove-Item Env:MANUAL_MODE -ErrorAction SilentlyContinue
    Remove-Item Env:DAILY_MODE -ErrorAction SilentlyContinue
}

git add -- data/archive.json data/monitor_state.json data/latest.json
git diff --cached --quiet
$diffExitCode = $LASTEXITCODE
if ($diffExitCode -eq 0) {
    Write-Host "There is no new monitoring data." -ForegroundColor Yellow
    exit 0
}
if ($diffExitCode -ne 1) {
    throw "Failed to inspect the monitoring data changes."
}

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss K"
git commit -m "Update CUTIE STREET monitoring data ($timestamp)"
if ($LASTEXITCODE -ne 0) {
    throw "Failed to commit the monitoring data."
}
git push origin main
if ($LASTEXITCODE -ne 0) {
    throw "Failed to send the monitoring data to GitHub."
}

Write-Host "The monitoring data was saved to GitHub." -ForegroundColor Green
