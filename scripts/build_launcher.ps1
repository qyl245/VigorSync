$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "python-env.ps1")
$python = Resolve-VigorSyncPython -ProjectRoot $projectRoot
$launcher = Join-Path $projectRoot "apps\desktop\launcher.py"
$dist = Join-Path $projectRoot "dist\VigorSyncLauncher"
$condaBin = "D:\Anaconda\Library\bin"
$runtimeDlls = @(
    "ffi-8.dll",
    "liblzma.dll",
    "libbz2.dll",
    "libexpat.dll"
)

if (-not (Test-Path -LiteralPath $python)) {
    throw "VigorSync Python interpreter was not found."
}

Push-Location $projectRoot
try {
    $arguments = @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name", "VigorSyncLauncher",
        "--distpath", $dist
    )
    foreach ($dll in $runtimeDlls) {
        $path = Join-Path $condaBin $dll
        if (Test-Path -LiteralPath $path) {
            $arguments += @("--add-binary", "$path;.")
        }
    }
    $arguments += $launcher
    & $python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }
    Write-Host "Built: $(Join-Path $dist 'VigorSyncLauncher.exe')"
}
finally {
    Pop-Location
}
