param(
    [ValidateSet("start", "stop", "status")]
    [string]$Action = "start",
    [switch]$NoBrowser,
    [switch]$Windowed,
    [switch]$DisableOfflineAnalysis
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "python-env.ps1")
$runtimeDir = Join-Path $projectRoot "webdata\runtime"
$statePath = Join-Path $runtimeDir "mirror-state.json"
$liveLog = Join-Path $runtimeDir "live-engine.log"
$liveErr = Join-Path $runtimeDir "live-engine.err.log"
$coreLog = Join-Path $runtimeDir "core-api.log"
$coreErr = Join-Path $runtimeDir "core-api.err.log"
$offlineLog = Join-Path $runtimeDir "offline-worker.log"
$offlineErr = Join-Path $runtimeDir "offline-worker.err.log"
$mirrorLog = Join-Path $runtimeDir "mirror.log"
$mirrorErr = Join-Path $runtimeDir "mirror.err.log"
$browserProfile = Join-Path $runtimeDir "browser-profile"
$python = Resolve-VigorSyncPython -ProjectRoot $projectRoot
$mirrorUrl = "http://127.0.0.1:8080"
$liveHealth = "http://127.0.0.1:8020/api/v1/health"
$coreHealth = "http://127.0.0.1:8011/api/v1/health"
$offlineHealth = "http://127.0.0.1:8030/api/v1/health"
$offlineToken = "vigorsync-offline-dev"

function Test-HttpOk {
    param([string]$Url)
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 400
    }
    catch {
        return $false
    }
}

function Get-PortOwner {
    param([int]$Port)
    try {
        return (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop |
            Select-Object -First 1 -ExpandProperty OwningProcess)
    }
    catch {
        return $null
    }
}

function Wait-Until {
    param(
        [scriptblock]$Condition,
        [int]$TimeoutSeconds = 25
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        if (& $Condition) {
            return $true
        }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)
    return $false
}

function Read-State {
    if (-not (Test-Path -LiteralPath $statePath)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Find-Browser {
    $candidates = @(
        "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "C:\Program Files\Google\Chrome\Application\chrome.exe",
        "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
    )
    return $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}

function Stop-ExactProcess {
    param(
        [int]$ProcessId,
        [string]$ExpectedText
    )
    if (-not $ProcessId) {
        return
    }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        return
    }
    if ($ExpectedText -and $process.CommandLine -notlike "*$ExpectedText*") {
        Write-Warning "PID $ProcessId no longer matches $ExpectedText; skipped."
        return
    }
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Test-ExactProcess {
    param(
        [int]$ProcessId,
        [string]$ExpectedText
    )
    if (-not $ProcessId) {
        return $false
    }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    return $null -ne $process -and
        $process.CommandLine -and
        $process.CommandLine -like "*$ExpectedText*"
}

function Stop-ManagedMirror {
    $state = Read-State
    if ($null -eq $state) {
        Write-Host "No managed mirror session was found."
        return
    }

    if ($state.browserProfile) {
        $browserProcesses = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object {
                $_.Name -in @("msedge.exe", "chrome.exe") -and
                $_.CommandLine -and
                $_.CommandLine.Contains($state.browserProfile)
            }
        foreach ($browserProcess in $browserProcesses) {
            Stop-Process -Id $browserProcess.ProcessId -Force -ErrorAction SilentlyContinue
        }
    }

    Stop-ExactProcess -ProcessId ([int]$state.mirrorPid) -ExpectedText "apps\mirror\server.py"
    Stop-ExactProcess -ProcessId ([int]$state.livePid) -ExpectedText "vigorsync_live"
    Stop-ExactProcess -ProcessId ([int]$state.offlinePid) -ExpectedText "vigorsync_offline"
    Stop-ExactProcess -ProcessId ([int]$state.corePid) -ExpectedText "vigorsync_core"

    Remove-Item -LiteralPath $statePath -Force -ErrorAction SilentlyContinue
    Write-Host "VigorSync mirror stopped."
}

function Show-Status {
    $state = Read-State
    [PSCustomObject]@{
        managed = $null -ne $state
        live_port_owner = Get-PortOwner -Port 8020
        mirror_port_owner = Get-PortOwner -Port 8080
        core_port_owner = Get-PortOwner -Port 8011
        live_ready = Test-HttpOk -Url $liveHealth
        offline_ready = Test-HttpOk -Url $offlineHealth
        offline_analysis_enabled = if ($null -eq $state) { $null } else { $state.offlineAnalysisEnabled }
        mirror_ready = Test-HttpOk -Url $mirrorUrl
        core_ready = Test-HttpOk -Url $coreHealth
        url = $mirrorUrl
    } | Format-List
}

if ($Action -eq "stop") {
    Stop-ManagedMirror
    exit 0
}

if ($Action -eq "status") {
    Show-Status
    exit 0
}

if (-not (Test-Path -LiteralPath $python)) {
    throw "VigorSync Python interpreter was not found at $python"
}

New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
New-Item -ItemType Directory -Force -Path $browserProfile | Out-Null

$state = Read-State
if ($null -eq $state) {
    $state = [PSCustomObject]@{
        startedAt = $null
        livePid = $null
        mirrorPid = $null
        corePid = $null
        offlinePid = $null
        offlineAnalysisEnabled = $true
        browserProfile = $browserProfile
    }
}
else {
    foreach ($pidField in @("livePid", "mirrorPid", "corePid", "offlinePid")) {
        if ($state.PSObject.Properties.Name -notcontains $pidField) {
            $state | Add-Member -NotePropertyName $pidField -NotePropertyValue $null
        }
    }
}
$state.startedAt = (Get-Date).ToString("o")
$state.offlineAnalysisEnabled = -not $DisableOfflineAnalysis

$startedLive = $null
$startedMirror = $null
$startedCore = $null
$startedOffline = $null

try {
    if (-not $DisableOfflineAnalysis -and -not (Test-HttpOk -Url $offlineHealth)) {
        if (Get-PortOwner -Port 8030) {
            throw "Port 8030 is occupied but the Offline Worker health check failed."
        }
        $oldPythonPath = $env:PYTHONPATH
        $oldSessionRoot = $env:VIGORYSYNC_SESSION_ROOT
        $oldOfflineRoot = $env:VLS_WEBDATA_ROOT
        $oldOfflineToken = $env:VIGORYSYNC_OFFLINE_INTERNAL_TOKEN
        $env:PYTHONPATH = Join-Path $projectRoot "services\offline-worker\src"
        $env:VIGORYSYNC_SESSION_ROOT = Join-Path $projectRoot "webdata\sessions"
        $env:VLS_WEBDATA_ROOT = Join-Path $projectRoot "webdata\offline"
        $env:VIGORYSYNC_OFFLINE_INTERNAL_TOKEN = $offlineToken
        try {
            $startedOffline = Start-Process `
                -FilePath $python `
                -ArgumentList @("-m", "vigorsync_offline") `
                -WorkingDirectory $projectRoot `
                -WindowStyle Hidden `
                -RedirectStandardOutput $offlineLog `
                -RedirectStandardError $offlineErr `
                -PassThru
        }
        finally {
            $env:PYTHONPATH = $oldPythonPath
            $env:VIGORYSYNC_SESSION_ROOT = $oldSessionRoot
            $env:VLS_WEBDATA_ROOT = $oldOfflineRoot
            $env:VIGORYSYNC_OFFLINE_INTERNAL_TOKEN = $oldOfflineToken
        }
        $state.offlinePid = $startedOffline.Id
        if (-not (Wait-Until -Condition { Test-HttpOk -Url $offlineHealth })) {
            throw "Offline Worker did not become ready. Check $offlineErr"
        }
        $state.offlinePid = Get-PortOwner -Port 8030
    }

    if (-not (Test-HttpOk -Url $coreHealth)) {
        if (Get-PortOwner -Port 8011) {
            throw "Port 8011 is occupied but the VigorSync Core API health check failed."
        }
        $oldPythonPath = $env:PYTHONPATH
        $oldOfflineUrl = $env:VIGORYSYNC_OFFLINE_URL
        $oldOfflineToken = $env:VIGORYSYNC_OFFLINE_INTERNAL_TOKEN
        $oldOfflineRoot = $env:VIGORYSYNC_OFFLINE_DATA_ROOT
        $oldOfflineAnalysisEnabled = $env:VIGORYSYNC_OFFLINE_ANALYSIS_ENABLED
        $env:PYTHONPATH = Join-Path $projectRoot "services\core-api\src"
        $env:VIGORYSYNC_OFFLINE_URL = "http://127.0.0.1:8030"
        $env:VIGORYSYNC_OFFLINE_INTERNAL_TOKEN = $offlineToken
        $env:VIGORYSYNC_OFFLINE_DATA_ROOT = Join-Path $projectRoot "webdata\offline"
        $env:VIGORYSYNC_OFFLINE_ANALYSIS_ENABLED = if ($DisableOfflineAnalysis) { "0" } else { "1" }
        try {
            $startedCore = Start-Process `
                -FilePath $python `
                -ArgumentList @("-m", "vigorsync_core") `
                -WorkingDirectory $projectRoot `
                -WindowStyle Hidden `
                -RedirectStandardOutput $coreLog `
                -RedirectStandardError $coreErr `
                -PassThru
        }
        finally {
            $env:PYTHONPATH = $oldPythonPath
            $env:VIGORYSYNC_OFFLINE_URL = $oldOfflineUrl
            $env:VIGORYSYNC_OFFLINE_INTERNAL_TOKEN = $oldOfflineToken
            $env:VIGORYSYNC_OFFLINE_DATA_ROOT = $oldOfflineRoot
            $env:VIGORYSYNC_OFFLINE_ANALYSIS_ENABLED = $oldOfflineAnalysisEnabled
        }
        $state.corePid = $startedCore.Id
        if (-not (Wait-Until -Condition { Test-HttpOk -Url $coreHealth })) {
            throw "Core API did not become ready. Check $coreErr"
        }
        $state.corePid = Get-PortOwner -Port 8011
    }
    else {
        $state.corePid = Get-PortOwner -Port 8011
    }

    if (-not (Test-HttpOk -Url $liveHealth)) {
        if (Get-PortOwner -Port 8020) {
            throw "Port 8020 is occupied but the VigorSync Live Engine health check failed."
        }
        $oldPythonPath = $env:PYTHONPATH
        $env:PYTHONPATH = @(
            (Join-Path $projectRoot "services\live-engine\src"),
            (Join-Path $projectRoot "services\core-api\src")
        ) -join [IO.Path]::PathSeparator
        $env:VIGORSYNC_CORE_URL = "http://127.0.0.1:8011"
        try {
            $startedLive = Start-Process `
                -FilePath $python `
                -ArgumentList @("-m", "vigorsync_live") `
                -WorkingDirectory $projectRoot `
                -WindowStyle Hidden `
                -RedirectStandardOutput $liveLog `
                -RedirectStandardError $liveErr `
                -PassThru
        }
        finally {
            $env:PYTHONPATH = $oldPythonPath
        }
        $state.livePid = $startedLive.Id
        if (-not (Wait-Until -Condition { Test-HttpOk -Url $liveHealth })) {
            throw "Live Engine did not become ready. Check $liveErr"
        }
        $state.livePid = Get-PortOwner -Port 8020
    }
    elseif (-not (Test-ExactProcess -ProcessId ([int]$state.livePid) -ExpectedText "vigorsync_live")) {
        $state.livePid = Get-PortOwner -Port 8020
    }

    if (-not (Test-HttpOk -Url $mirrorUrl)) {
        if (Get-PortOwner -Port 8080) {
            throw "Port 8080 is occupied but the mirror page is not reachable."
        }
        $startedMirror = Start-Process `
            -FilePath $python `
            -ArgumentList @("apps\mirror\server.py") `
            -WorkingDirectory $projectRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $mirrorLog `
            -RedirectStandardError $mirrorErr `
            -PassThru
        $state.mirrorPid = $startedMirror.Id
        if (-not (Wait-Until -Condition { Test-HttpOk -Url $mirrorUrl })) {
            throw "Mirror page did not become ready. Check $mirrorErr"
        }
        $state.mirrorPid = Get-PortOwner -Port 8080
    }
    elseif (-not (Test-ExactProcess -ProcessId ([int]$state.mirrorPid) -ExpectedText "apps\mirror\server.py")) {
        $state.mirrorPid = Get-PortOwner -Port 8080
    }

    $state | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8

    if (-not $NoBrowser) {
        $browser = Find-Browser
        if (-not $browser) {
            throw "Microsoft Edge or Google Chrome was not found."
        }
        $browserMode = if ($Windowed) { "--app=$mirrorUrl" } else { "--kiosk" }
        $browserArguments = @(
            $browserMode,
            "--user-data-dir=`"$browserProfile`"",
            "--no-first-run",
            "--no-default-browser-check",
            $mirrorUrl
        )
        Start-Process -FilePath $browser -ArgumentList $browserArguments | Out-Null
    }

    Write-Host "VigorSync mirror is running at $mirrorUrl"
    Write-Host "Logs: $runtimeDir"
}
catch {
    if ($startedMirror) {
        $mirrorOwner = Get-PortOwner -Port 8080
        Stop-ExactProcess -ProcessId $mirrorOwner -ExpectedText "apps\mirror\server.py"
    }
    if ($startedLive) {
        $liveOwner = Get-PortOwner -Port 8020
        Stop-ExactProcess -ProcessId $liveOwner -ExpectedText "vigorsync_live"
    }
    if ($startedOffline) {
        $offlineOwner = Get-PortOwner -Port 8030
        Stop-ExactProcess -ProcessId $offlineOwner -ExpectedText "vigorsync_offline"
    }
    if ($startedCore) {
        $coreOwner = Get-PortOwner -Port 8011
        Stop-ExactProcess -ProcessId $coreOwner -ExpectedText "vigorsync_core"
    }
    throw
}
