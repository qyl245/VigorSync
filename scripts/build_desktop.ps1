param(
    [switch]$OneFile
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "VigorSync .venv was not found."
}

$mode = if ($OneFile) { "--onefile" } else { "--onedir" }
$arguments = @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--windowed",
    $mode,
    "--name", "VigorSyncDesktop",
    "--paths", (Join-Path $projectRoot "services\live-engine\src"),
    "--add-data", "$(Join-Path $projectRoot 'apps\mirror\web');apps\mirror\web",
    "--collect-all", "mediapipe",
    "--collect-all", "cv2",
    "--hidden-import", "uvicorn.logging",
    "--hidden-import", "uvicorn.loops.auto",
    "--hidden-import", "uvicorn.protocols.http.auto",
    "--hidden-import", "uvicorn.protocols.websockets.auto",
    "--hidden-import", "uvicorn.lifespan.on",
    (Join-Path $projectRoot "apps\desktop\app.py")
)

Push-Location $projectRoot
try {
    & $python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }
    $target = if ($OneFile) {
        Join-Path $projectRoot "dist\VigorSyncDesktop.exe"
    } else {
        Join-Path $projectRoot "dist\VigorSyncDesktop\VigorSyncDesktop.exe"
    }
    Write-Host "Built: $target"
}
finally {
    Pop-Location
}
