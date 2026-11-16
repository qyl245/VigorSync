$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "VigorSync .venv was not found. Create it with D:\Anaconda\python.exe -m venv .venv."
}

Push-Location $projectRoot
try {
    & $python -B scripts\validate_contracts.py
    if ($LASTEXITCODE -ne 0) { throw "Contract validation failed." }
    & $python -B -m unittest discover -s tests\contracts -v
    if ($LASTEXITCODE -ne 0) { throw "Contract tests failed." }

    $env:PYTHONPATH = "services\live-engine\src"
    & $python -B -m unittest discover -s services\live-engine\tests -v
    if ($LASTEXITCODE -ne 0) { throw "M1 tests failed." }

    node --check apps\mirror\web\app.js
    if ($LASTEXITCODE -ne 0) { throw "Mirror JavaScript syntax check failed." }
    Write-Host "VigorSync M1 checks passed."
}
finally {
    Pop-Location
}
