$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repositoryRoot

$venvPython = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "初回設定が必要です。先にsetup-local.cmdを実行してください。"
}

Write-Host "GitHubから最新データを確認しています..." -ForegroundColor Cyan
git pull --ff-only origin main
if ($LASTEXITCODE -ne 0) {
    throw "GitHubから最新データを取得できませんでした。"
}

$env:MANUAL_MODE = "true"
$env:DAILY_MODE = "true"
try {
    & $venvPython monitor.py
    if ($LASTEXITCODE -ne 0) {
        throw "Xの投稿取得に失敗しました。diagnosticsフォルダーを確認してください。"
    }
}
finally {
    Remove-Item Env:MANUAL_MODE -ErrorAction SilentlyContinue
    Remove-Item Env:DAILY_MODE -ErrorAction SilentlyContinue
}

git add -- data/archive.json data/monitor_state.json data/latest.json
git diff --cached --quiet
if ($LASTEXITCODE -eq 0) {
    Write-Host "新しい保存データはありませんでした。" -ForegroundColor Yellow
    exit 0
}

$timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss K"
git commit -m "Update CUTIE STREET monitoring data ($timestamp)"
if ($LASTEXITCODE -ne 0) {
    throw "取得データのコミットに失敗しました。"
}
git push origin main
if ($LASTEXITCODE -ne 0) {
    throw "取得データをGitHubへ送信できませんでした。"
}

Write-Host "取得結果をGitHubへ保存しました。" -ForegroundColor Green
