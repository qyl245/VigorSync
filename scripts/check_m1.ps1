$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "python-env.ps1")
$python = Resolve-VigorSyncPython -ProjectRoot $projectRoot

if (-not (Test-Path -LiteralPath $python)) {
    throw "VigorSync Python interpreter was not found at $python."
}

Push-Location $projectRoot
try {
    & $python -B scripts\validate_contracts.py
    if ($LASTEXITCODE -ne 0) { throw "Contract validation failed." }
    & $python -B -m unittest discover -s tests\contracts -v
    if ($LASTEXITCODE -ne 0) { throw "Contract tests failed." }

    $env:PYTHONPATH = "services\live-engine\src;services\core-api\src"
    & $python -B -m unittest discover -s services\core-api\tests -v
    if ($LASTEXITCODE -ne 0) { throw "Core API tests failed." }

    & $python -B -m unittest discover -s services\live-engine\tests -v
    if ($LASTEXITCODE -ne 0) { throw "M1 tests failed." }

    node --check apps\mirror\web\app.js
    if ($LASTEXITCODE -ne 0) { throw "Mirror JavaScript syntax check failed." }
    Write-Host "VigorSync M1 checks passed."
}
finally {
    Pop-Location
}
