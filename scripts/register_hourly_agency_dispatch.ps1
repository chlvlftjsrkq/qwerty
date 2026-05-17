param(
    [string]$TaskName = "qwerty Hourly Agency GitHub Dispatch",
    [string]$StartTime = "",
    [int]$IntervalHours = 1
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$DispatchScript = Join-Path $PSScriptRoot "dispatch_hourly_agency_check.ps1"

if (!(Test-Path $DispatchScript)) {
    throw "Dispatch script was not found: $DispatchScript"
}

if ([string]::IsNullOrWhiteSpace($StartTime)) {
    $now = Get-Date
    $candidate = Get-Date -Hour $now.Hour -Minute 7 -Second 0
    if ($candidate -le $now) {
        $candidate = $candidate.AddHours(1)
    }
} else {
    $candidate = [datetime]::Parse($StartTime)
}

$PowerShell = Join-Path $PSHOME "powershell.exe"
$Action = New-ScheduledTaskAction `
    -Execute $PowerShell `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$DispatchScript`"" `
    -WorkingDirectory $Root

$Trigger = New-ScheduledTaskTrigger `
    -Once `
    -At $candidate `
    -RepetitionInterval (New-TimeSpan -Hours $IntervalHours) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Dispatches the qwerty hourly agency GitHub Actions schedule-check workflow every hour." `
    -Force | Out-Null

Write-Host ("등록 완료: {0} / 시작 {1} / {2}시간마다 반복" -f $TaskName, $candidate.ToString("yyyy-MM-dd HH:mm:ss"), $IntervalHours)
