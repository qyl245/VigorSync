param(
    [Parameter(Mandatory = $true)]
    [string]$ItemsBase64,
    [string]$Voice,
    [int]$Rate = 0,
    [double]$Volume = 1.0
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech

$json = [Text.Encoding]::UTF8.GetString(
    [Convert]::FromBase64String($ItemsBase64)
)
$parsed = $json | ConvertFrom-Json
$items = @($parsed)
if ($items.Count -eq 1 -and $items[0] -is [System.Array]) {
    $items = @($items[0])
}
if ($items.Count -eq 0) {
    throw "No speech items were provided."
}

$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $installed = @($synth.GetInstalledVoices())
    $selected = $null
    if ($Voice) {
        $selected = $installed |
            ForEach-Object { $_.VoiceInfo } |
            Where-Object {
                $_.Name.IndexOf($Voice, [StringComparison]::OrdinalIgnoreCase) -ge 0
            } |
            Select-Object -First 1
    }
    if ($null -eq $selected) {
        $selected = $installed |
            ForEach-Object { $_.VoiceInfo } |
            Where-Object { $_.Culture.Name -eq "zh-CN" } |
            Select-Object -First 1
    }
    if ($null -ne $selected) {
        $synth.SelectVoice($selected.Name)
    }

    $synth.Rate = [Math]::Max(-10, [Math]::Min(10, $Rate))
    $synth.Volume = [Math]::Max(0, [Math]::Min(100, [Math]::Round($Volume * 100)))

    foreach ($item in $items) {
        $path = [string]$item.path
        $synth.SetOutputToWaveFile($path)
        $synth.Speak([string]$item.text)
        $synth.SetOutputToNull()
        $file = Get-Item -LiteralPath $path
        if ($file.Length -le 44) {
            throw "Speech synthesis produced an empty WAV: $path"
        }
    }
}
finally {
    $synth.SetOutputToNull()
    $synth.Dispose()
}
