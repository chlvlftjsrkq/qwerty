param(
    [string]$TaskName = "Qwerty Negative MMA News Watch 5min Test",
    [int]$IntervalMinutes = 5,
    [string]$Repo = "chlvlftjsrkq/qwerty",
    [string]$Workflow = "negative-news-watch.yml",
    [string]$Ref = "main",
    [string]$GhExe = "C:\Program Files\GitHub CLI\gh.exe",
    [string]$TargetChatroom = "test",
    [int]$MaxAlerts = 1,
    [int]$LookbackHours = 168,
    [switch]$DryRun,
    [switch]$StartNow
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$DispatchScript = Join-Path $Root "scripts\dispatch_negative_watch.ps1"
if (!(Test-Path -LiteralPath $DispatchScript)) {
    throw "Dispatch script not found: $DispatchScript"
}
if (!(Test-Path -LiteralPath $GhExe)) {
    throw "GitHub CLI not found: $GhExe"
}

$dryRunValue = if ($DryRun) { "true" } else { "false" }
$arguments = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$DispatchScript`"",
    "-Repo", "`"$Repo`"",
    "-Workflow", "`"$Workflow`"",
    "-Ref", "`"$Ref`"",
    "-GhExe", "`"$GhExe`"",
    "-TargetChatroom", "`"$TargetChatroom`"",
    "-MaxAlerts", "$MaxAlerts",
    "-LookbackHours", "$LookbackHours",
    "-DryRun", "$dryRunValue",
    "-TriggerSource", "pc-negative-watch-5min-test"
)

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument ($arguments -join " ") `
    -WorkingDirectory $Root

$startAt = (Get-Date).AddMinutes(1)
$trigger = New-ScheduledTaskTrigger -Once -At $startAt `
    -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Dispatches the qwerty negative MMA news watch workflow every $IntervalMinutes minutes." `
    -Force | Out-Null

Write-Host "Registered task: $TaskName"
Write-Host "Interval minutes: $IntervalMinutes"
Write-Host "First scheduled run: $startAt"
Write-Host "Target chatroom: $TargetChatroom"
Write-Host "Workflow target: $Repo / $Workflow"

if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Requested immediate run: $TaskName"
}
