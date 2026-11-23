param(
    [string]$Version = "",
    [string]$InnoCompiler = "",
    [string]$Compression = "lzma2/normal",
    [switch]$SkipBuild,
    [switch]$SkipSmokeTest
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$desktopRoot = Join-Path $projectRoot "dist\VigorSyncDesktop"
$desktopExe = Join-Path $desktopRoot "VigorSyncDesktop.exe"
$distRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot "dist"))
$stagingRoot = [IO.Path]::GetFullPath(
    (Join-Path $distRoot "installer-staging\VigorSyncMirror")
)
$installerRoot = Join-Path $distRoot "installer"
$issPath = Join-Path $projectRoot "packaging\windows\VigorSyncMirror.iss"

function Assert-ChildPath {
    param(
        [string]$Path,
        [string]$Root
    )

    $normalizedRoot = [IO.Path]::GetFullPath($Root).TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
    $normalizedPath = [IO.Path]::GetFullPath($Path)
    $prefix = $normalizedRoot + [IO.Path]::DirectorySeparatorChar
    if (-not $normalizedPath.StartsWith(
        $prefix,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Refusing to operate outside $normalizedRoot`: $normalizedPath"
    }
}

function Resolve-InnoCompiler {
    param([string]$ExplicitPath)

    $candidates = @()
    if ($ExplicitPath) {
        $candidates += $ExplicitPath
    }
    if ($env:INNO_SETUP_COMPILER) {
        $candidates += $env:INNO_SETUP_COMPILER
    }
    if ($env:LOCALAPPDATA) {
        $candidates += Join-Path `
            $env:LOCALAPPDATA `
            "Programs\Inno Setup 6\ISCC.exe"
    }
    if (${env:ProgramFiles(x86)}) {
        $candidates += Join-Path `
            ${env:ProgramFiles(x86)} `
            "Inno Setup 6\ISCC.exe"
    }
    if ($env:ProgramFiles) {
        $candidates += Join-Path `
            $env:ProgramFiles `
            "Inno Setup 6\ISCC.exe"
    }

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    $command = Get-Command iscc.exe -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    throw "Inno Setup 6 compiler (ISCC.exe) was not found."
}

if (-not $Version) {
    $pyproject = Get-Content `
        -LiteralPath (Join-Path $projectRoot "services\live-engine\pyproject.toml") `
        -Raw
    $match = [regex]::Match($pyproject, '(?m)^version\s*=\s*"([^"]+)"')
    $Version = if ($match.Success) { $match.Groups[1].Value } else { "0.1.0" }
}
if ($Version -notmatch '^\d+\.\d+\.\d+(\.\d+)?$') {
    throw "Version must contain two to four numeric components: $Version"
}

if (-not $SkipBuild) {
    & (Join-Path $PSScriptRoot "build_desktop.ps1")
    if ($LASTEXITCODE -ne 0) {
        throw "Desktop build failed with exit code $LASTEXITCODE."
    }
}
if (-not (Test-Path -LiteralPath $desktopExe)) {
    throw "Desktop executable was not found: $desktopExe"
}

if (-not $SkipSmokeTest) {
    $running = Get-Process -Name "VigorSyncDesktop" -ErrorAction SilentlyContinue
    if ($running) {
        throw "Close all VigorSyncDesktop processes before running the packaged smoke test."
    }
    $smoke = Start-Process `
        -FilePath $desktopExe `
        -ArgumentList @("--headless", "--seconds", "3") `
        -Wait `
        -PassThru
    if ($smoke.ExitCode -ne 0) {
        throw "Packaged desktop smoke test failed with exit code $($smoke.ExitCode)."
    }
}

Assert-ChildPath -Path $stagingRoot -Root $distRoot
if (Test-Path -LiteralPath $stagingRoot) {
    Remove-Item -LiteralPath $stagingRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $stagingRoot -Force | Out-Null
Copy-Item `
    -Path (Join-Path $desktopRoot "*") `
    -Destination $stagingRoot `
    -Recurse `
    -Force

$cacheDirs = Get-ChildItem `
    -LiteralPath $stagingRoot `
    -Directory `
    -Recurse `
    -Filter "__pycache__"
foreach ($cacheDir in $cacheDirs) {
    Assert-ChildPath -Path $cacheDir.FullName -Root $stagingRoot
    Remove-Item -LiteralPath $cacheDir.FullName -Recurse -Force
}

$commit = "unknown"
$dirty = $false
try {
    $commit = (
        & git -C $projectRoot rev-parse --short=12 HEAD
    ).Trim()
    $dirty = [bool](
        & git -C $projectRoot status --porcelain
    )
}
catch {
    Write-Warning "Git metadata is unavailable; the package manifest will use unknown."
}

$mainExe = Join-Path $stagingRoot "VigorSyncDesktop.exe"
$manifest = [ordered]@{
    product = "VigorSync Mirror"
    version = $Version
    commit = $commit
    dirty = $dirty
    built_at = (Get-Date).ToUniversalTime().ToString("o")
    executable_sha256 = (
        Get-FileHash -LiteralPath $mainExe -Algorithm SHA256
    ).Hash.ToLowerInvariant()
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content `
    -LiteralPath (Join-Path $stagingRoot "VERSION.json") `
    -Encoding UTF8
Copy-Item `
    -LiteralPath (Join-Path $projectRoot "packaging\windows\info-after.txt") `
    -Destination (Join-Path $stagingRoot "INSTALL-NOTES.txt") `
    -Force

$iscc = Resolve-InnoCompiler -ExplicitPath $InnoCompiler
New-Item -ItemType Directory -Path $installerRoot -Force | Out-Null
& $iscc `
    "/DMyAppVersion=$Version" `
    "/DCompressionMode=$Compression" `
    $issPath
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed with exit code $LASTEXITCODE."
}

$artifact = Join-Path `
    $installerRoot `
    "VigorSync-Mirror-$Version-win-x64-setup.exe"
if (-not (Test-Path -LiteralPath $artifact)) {
    throw "Installer artifact was not found: $artifact"
}
$hash = (
    Get-FileHash -LiteralPath $artifact -Algorithm SHA256
).Hash.ToLowerInvariant()
"$hash *VigorSync-Mirror-$Version-win-x64-setup.exe" | Set-Content `
    -LiteralPath "$artifact.sha256" `
    -Encoding ASCII

$sizeMb = [math]::Round(
    (Get-Item -LiteralPath $artifact).Length / 1MB,
    1
)
Write-Host "Built installer: $artifact ($sizeMb MB)"
Write-Host "SHA-256: $hash"
