$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

function Find-Python {
    $knownPath = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"
    if (Test-Path -LiteralPath $knownPath) {
        return $knownPath
    }

    $python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($python) {
        $version = & $python.Source --version 2>&1
        if ($LASTEXITCODE -eq 0 -and $version -match "Python 3\.") {
            return $python.Source
        }
    }

    return $null
}

$pythonPath = Find-Python
if (-not $pythonPath) {
    Write-Host "Python 3.12をインストールします。" -ForegroundColor Cyan
    winget install --exact --id Python.Python.3.12 --source winget
    if ($LASTEXITCODE -ne 0) {
        throw "Pythonのインストールに失敗しました。"
    }
    $pythonPath = Find-Python
}

if (-not $pythonPath) {
    throw "Pythonが見つかりません。PCを再起動してsetup-local.cmdを再実行してください。"
}

if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    & $pythonPath -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        throw "Python実行環境の作成に失敗しました。"
    }
}

$venvPython = Join-Path (Get-Location) ".venv\Scripts\python.exe"
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "pipの更新に失敗しました。"
}
& $venvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    throw "必要なライブラリのインストールに失敗しました。"
}
& $venvPython -m playwright install chromium
if ($LASTEXITCODE -ne 0) {
    throw "ブラウザーのインストールに失敗しました。"
}

Write-Host "初回設定が完了しました。run-local.cmdをダブルクリックしてください。" -ForegroundColor Green
