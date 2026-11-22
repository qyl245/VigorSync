param(
    [string]$AvdName = $env:VIGORSYNC_AVD,
    [switch]$List,
    [switch]$StartServices,
    [switch]$InstallApp,
    [int]$TimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$androidPackage = "com.vigorsync.vigorsync_mobile"
$apkPath = Join-Path $projectRoot "apps\mobile\build\app\outputs\flutter-apk\app-debug.apk"

function Resolve-AndroidSdk {
    $sdkCandidates = @(
        $env:ANDROID_SDK_ROOT,
        $env:ANDROID_HOME,
        "D:\app\Android\SDK",
        "D:\Program\Android\SDK",
        (Join-Path $env:LOCALAPPDATA "Android\Sdk")
    ) | Where-Object { $_ } | Select-Object -Unique

    foreach ($sdkRoot in $sdkCandidates) {
        $emulator = Join-Path $sdkRoot "emulator\emulator.exe"
        $adb = Join-Path $sdkRoot "platform-tools\adb.exe"
        if ((Test-Path -LiteralPath $emulator) -and (Test-Path -LiteralPath $adb)) {
            return [PSCustomObject]@{
                Root = $sdkRoot
                Emulator = $emulator
                Adb = $adb
            }
        }
    }

    throw "Android SDK was not found. Set ANDROID_SDK_ROOT or ANDROID_HOME."
}

function Get-AdbDeviceSerials {
    param([string]$Adb)

    $devices = & $Adb devices 2>$null
    foreach ($line in $devices) {
        if ($line -match "^(emulator-\d+)\s+device\s*$") {
            $matches[1]
        }
    }
}

function Get-RunningAvdName {
    param(
        [string]$Adb,
        [string]$Serial
    )

    $output = & $Adb -s $Serial emu avd name 2>$null
    return $output |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ -and $_ -ne "OK" } |
        Select-Object -First 1
}

function Find-RunningAvd {
    param(
        [string]$Adb,
        [string]$TargetAvd
    )

    foreach ($serial in Get-AdbDeviceSerials -Adb $Adb) {
        if ((Get-RunningAvdName -Adb $Adb -Serial $serial) -eq $TargetAvd) {
            return $serial
        }
    }
    return $null
}

function Wait-ForAvd {
    param(
        [string]$Adb,
        [string]$TargetAvd,
        [System.Diagnostics.Process]$EmulatorProcess,
        [int]$TimeoutSeconds
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        foreach ($serial in Get-AdbDeviceSerials -Adb $Adb) {
            $runningAvd = Get-RunningAvdName -Adb $Adb -Serial $serial
            if ($runningAvd -ne $TargetAvd) {
                continue
            }
            $bootCompleted = (& $Adb -s $serial shell getprop sys.boot_completed 2>$null).Trim()
            if ($bootCompleted -eq "1") {
                return $serial
            }
        }

        if ($EmulatorProcess -and $EmulatorProcess.HasExited) {
            throw "Android emulator exited before boot completed (code $($EmulatorProcess.ExitCode))."
        }
        Start-Sleep -Seconds 2
    }

    throw "Timed out waiting for AVD '$TargetAvd' to finish booting."
}

$sdk = Resolve-AndroidSdk
& $sdk.Adb start-server | Out-Null

$availableAvds = @(
    & $sdk.Emulator -list-avds |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ }
)
if ($List) {
    $availableAvds
    exit 0
}
if ($availableAvds.Count -eq 0) {
    throw "No Android Virtual Devices are installed."
}
if (-not $AvdName) {
    if ($availableAvds.Count -eq 1) {
        $AvdName = $availableAvds[0]
    }
    else {
        throw "Multiple AVDs are available. Pass -AvdName or set VIGORSYNC_AVD.`n$($availableAvds -join "`n")"
    }
}
if ($AvdName -notin $availableAvds) {
    throw "AVD '$AvdName' was not found. Available: $($availableAvds -join ', ')"
}

$serial = Find-RunningAvd -Adb $sdk.Adb -TargetAvd $AvdName
$emulatorProcess = $null
if ($serial) {
    Write-Host "Reusing running AVD '$AvdName' at $serial."
}
else {
    Write-Host "Starting AVD '$AvdName'..."
    $emulatorProcess = Start-Process `
        -FilePath $sdk.Emulator `
        -ArgumentList @("-avd", $AvdName, "-netdelay", "none", "-netspeed", "full") `
        -PassThru
}

$serial = Wait-ForAvd `
    -Adb $sdk.Adb `
    -TargetAvd $AvdName `
    -EmulatorProcess $emulatorProcess `
    -TimeoutSeconds $TimeoutSeconds
& $sdk.Adb -s $serial shell input keyevent 82 | Out-Null

if ($StartServices) {
    & (Join-Path $PSScriptRoot "mirror-control.ps1") -Action start -NoBrowser
}

if ($InstallApp) {
    if (-not (Test-Path -LiteralPath $apkPath)) {
        throw "Debug APK was not found at $apkPath. Build it with 'flutter build apk --debug --no-pub'."
    }
    Write-Host "Installing $apkPath..."
    & $sdk.Adb -s $serial install -r $apkPath
    if ($LASTEXITCODE -ne 0) {
        throw "APK installation failed."
    }
    & $sdk.Adb -s $serial shell monkey -p $androidPackage -c android.intent.category.LAUNCHER 1 | Out-Null
}

Write-Host ""
Write-Host "AVD ready: $AvdName ($serial)"
Write-Host "Emulator Core URL: http://10.0.2.2:8011"
Write-Host "Development pairing code: 482913"
Write-Host "Run Flutter: D:\flutter\bin\flutter.bat run -d $serial"
