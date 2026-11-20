param(
    [switch]$BuildApk
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$mobileRoot = Join-Path $projectRoot "apps\mobile"

$flutterCandidates = @(
    "D:\flutter\bin\flutter.bat",
    "D:\Flutter\bin\flutter.bat",
    (Join-Path $projectRoot ".flutter\bin\flutter.bat"),
    "D:\dev\flutter\bin\flutter.bat"
)
$flutter = $flutterCandidates |
    Where-Object { Test-Path -LiteralPath $_ } |
    Select-Object -First 1
if (-not $flutter) {
    $flutter = (Get-Command flutter -ErrorAction SilentlyContinue).Source
}
if (-not $flutter) {
    throw "Flutter was not found. Add it to PATH or install it at D:\flutter."
}

& (Join-Path $PSScriptRoot "check_m1.ps1")
if ($LASTEXITCODE -ne 0) {
    throw "M1 and Core regression checks failed."
}

Push-Location $mobileRoot
try {
    & $flutter analyze
    if ($LASTEXITCODE -ne 0) {
        throw "flutter analyze failed."
    }

    & $flutter test
    if ($LASTEXITCODE -ne 0) {
        throw "flutter test failed."
    }

    if ($BuildApk) {
        if (-not $env:JAVA_HOME) {
            $env:JAVA_HOME = "D:\Program\Android\Android studio\jbr"
        }
        if (-not $env:ANDROID_HOME) {
            $env:ANDROID_HOME = "D:\Program\Android\SDK"
        }
        $env:ANDROID_SDK_ROOT = $env:ANDROID_HOME
        if (-not $env:FLUTTER_STORAGE_BASE_URL) {
            $env:FLUTTER_STORAGE_BASE_URL = "https://storage.flutter-io.cn"
        }
        & $flutter build apk --debug --no-pub
        if ($LASTEXITCODE -ne 0) {
            throw "flutter build apk failed."
        }
    }
}
finally {
    Pop-Location
}

Write-Host "VigorSync M2 checks passed."
