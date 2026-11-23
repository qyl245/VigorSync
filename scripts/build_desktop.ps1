param(
    [switch]$OneFile
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "python-env.ps1")
$python = Resolve-VigorSyncPython -ProjectRoot $projectRoot
$pythonBase = (& $python -c "import sys; print(sys.base_prefix)").Trim()
$runtimeBin = Join-Path $pythonBase "Library\bin"
$runtimeDlls = @(
    "ffi-8.dll",
    "liblzma.dll",
    "libbz2.dll",
    "libexpat.dll",
    "tcl86t.dll",
    "tk86t.dll"
)

if (-not (Test-Path -LiteralPath $python)) {
    throw "VigorSync Python interpreter was not found."
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
    "--paths", (Join-Path $projectRoot "services\core-api\src"),
    "--paths", (Join-Path $projectRoot "services\offline-worker\src"),
    "--paths", (Join-Path $projectRoot "services\offline-worker\vendor\vls\web\backend"),
    "--add-data", "$(Join-Path $projectRoot 'apps\mirror\web');apps\mirror\web",
    "--add-data", "$(Join-Path $projectRoot 'configs\issues');configs\issues",
    "--add-data", "$(Join-Path $projectRoot 'reference_data\templates');reference_data\templates",
    "--add-data", "$(Join-Path $projectRoot 'scripts\trajectory_compare.py');scripts",
    "--add-data", "$(Join-Path $projectRoot 'services\offline-worker\vendor\vls');services\offline-worker\vendor\vls",
    "--collect-all", "mediapipe",
    "--collect-all", "cv2",
    "--collect-all", "fastapi",
    "--collect-all", "starlette",
    "--collect-submodules", "vigorsync_core",
    "--collect-submodules", "vigorsync_live",
    "--collect-submodules", "vigorsync_offline",
    "--collect-submodules", "multipart",
    "--collect-submodules", "httpx",
    "--collect-submodules", "httpcore",
    "--collect-submodules", "anyio",
    "--hidden-import", "vigorsync_core.app",
    "--hidden-import", "vigorsync_live.app",
    "--hidden-import", "vigorsync_offline.app",
    "--hidden-import", "uvicorn.logging",
    "--hidden-import", "uvicorn.loops.auto",
    "--hidden-import", "uvicorn.protocols.http.auto",
    "--hidden-import", "uvicorn.protocols.websockets.auto",
    "--hidden-import", "uvicorn.lifespan.on",
    (Join-Path $projectRoot "apps\desktop\app.py")
)
$extraArguments = @()
foreach ($dll in $runtimeDlls) {
    $path = Join-Path $runtimeBin $dll
    if (Test-Path -LiteralPath $path) {
        $extraArguments += @("--add-binary", "$path;.")
    }
}
if ($extraArguments.Count -gt 0) {
    $entryPoint = $arguments[-1]
    $arguments = (
        $arguments[0..($arguments.Count - 2)] +
        $extraArguments +
        @($entryPoint)
    )
}

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
