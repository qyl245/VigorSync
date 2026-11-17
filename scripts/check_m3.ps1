param(
    [switch]$BuildApk
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$workerRoot = Join-Path $projectRoot "services\offline-worker"

if (-not (Test-Path -LiteralPath $python)) {
    throw "VigorSync .venv was not found."
}

$checkArgs = @()
if ($BuildApk) {
    $checkArgs += "-BuildApk"
}
& powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "check_m2.ps1") @checkArgs
if ($LASTEXITCODE -ne 0) {
    throw "M2 regression checks failed."
}

$oldPythonPath = $env:PYTHONPATH
$oldOfflineRoot = $env:VLS_WEBDATA_ROOT
try {
    $env:PYTHONPATH = Join-Path $workerRoot "src"
    $env:VLS_WEBDATA_ROOT = Join-Path $projectRoot "webdata\offline"
    & $python -B -c "from vigorsync_offline.app import app; assert app.title"
    if ($LASTEXITCODE -ne 0) {
        throw "Offline Worker import failed."
    }
    & $python -B -m unittest discover -s (Join-Path $workerRoot "tests") -v
    if ($LASTEXITCODE -ne 0) {
        throw "Offline Worker tests failed."
    }
}
finally {
    $env:PYTHONPATH = $oldPythonPath
    $env:VLS_WEBDATA_ROOT = $oldOfflineRoot
}

foreach ($required in @(
    "vendor\vls\models\pose_landmarker_full.task",
    "vendor\vls\configs\repchain_segmenter.json",
    "vendor\vls\web\backend\app\repchain\repchain_pipeline.py"
)) {
    if (-not (Test-Path -LiteralPath (Join-Path $workerRoot $required))) {
        throw "Offline Worker runtime file is missing: $required"
    }
}

Write-Host "VigorSync M3 checks passed."
