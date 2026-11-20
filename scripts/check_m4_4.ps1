param(
    [string]$JobId
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "VigorSync .venv was not found."
}

& $python -B (Join-Path $PSScriptRoot "sync_issue_configs.py") --check
if ($LASTEXITCODE -ne 0) {
    throw "Shared issue catalog and VLS configs have drifted."
}

& $python -B (Join-Path $PSScriptRoot "validate_contracts.py")
if ($LASTEXITCODE -ne 0) {
    throw "Contract validation failed."
}

& $python -B -m unittest discover -s (Join-Path $projectRoot "tests\contracts") -v
if ($LASTEXITCODE -ne 0) {
    throw "Issue consistency tests failed."
}

& $python -B -m unittest discover -s (Join-Path $projectRoot "services\offline-worker\tests") -v
if ($LASTEXITCODE -ne 0) {
    throw "Offline Worker tests failed."
}

& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "check_m3.ps1")
if ($LASTEXITCODE -ne 0) {
    throw "M3 regression checks failed."
}

if ($JobId) {
    $result = Invoke-RestMethod -Uri "http://127.0.0.1:8030/api/v1/jobs/$JobId/result"
    if ($null -eq $result.realtime_review) {
        throw "Job $JobId has no realtime_review result."
    }
    if ($result.realtime_review.state -ne "available") {
        throw "Job $JobId realtime review state is $($result.realtime_review.state)."
    }
    if (@($result.realtime_review.matched_codes).Count -eq 0) {
        throw "Job $JobId has no matched realtime/offline issue codes."
    }
    $report = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8030/api/v1/jobs/$JobId/feedback-report"
    if ($report.Content -notmatch "实时提示关联") {
        throw "Job $JobId report has no realtime review section."
    }
    Write-Host "Realtime correlation verified for $JobId."
}

Write-Host "VigorSync M4.4 checks passed."
