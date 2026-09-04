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
    Write-Host "Installing Python 3.12..." -ForegroundColor Cyan
    winget install --exact --id Python.Python.3.12 --source winget
    if ($LASTEXITCODE -ne 0) {
        throw "Python installation failed."
    }
    $pythonPath = Find-Python
}

if (-not $pythonPath) {
    throw "Python was not found. Restart the PC, then run setup-local.cmd again."
}

if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    & $pythonPath -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the Python environment."
    }
}

$venvPython = Join-Path (Get-Location) ".venv\Scripts\python.exe"
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "Failed to update pip."
}
& $venvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install the required Python package."
}
& $venvPython -m playwright install chromium
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install Chromium."
}

Write-Host "Setup completed. Double-click run-local.cmd." -ForegroundColor Green
