param(
    [string]$TaskName = "Qwerty Agency Kakao Workflow Dispatch 5min",
    [int]$IntervalMinutes = 5,
    [string]$Repo = "chlvlftjsrkq/qwerty",
    [string]$Workflow = "daily-post-mcp-self-hosted.yml",
    [string]$Ref = "main",
    [string]$GhExe = "C:\Program Files\GitHub CLI\gh.exe",
    [string]$TargetStartDate = "",
    [string]$TargetDate = "",
    [switch]$NoSummary,
    [switch]$NoPodcast,
    [switch]$NoImage,
    [switch]$NoArchive,
    [switch]$NoWeatherInSummary,
    [string]$TargetChatroom = "",
    [switch]$SkipNonBusinessDays,
    [switch]$StartNow
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$DispatchScript = Join-Path $Root "scripts\dispatch_agency_workflow.ps1"
if (!(Test-Path -LiteralPath $DispatchScript)) {
    throw "Dispatch script not found: $DispatchScript"
}
if (!(Test-Path -LiteralPath $GhExe)) {
    throw "GitHub CLI not found: $GhExe"
}

$sendSummaryArg = if ($NoSummary) { 'false' } else { 'true' }
$sendPodcastArg = if ($NoPodcast) { 'false' } else { 'true' }
$sendImageArg = if ($NoImage) { 'false' } else { 'true' }
$includeWeatherArg = if ($NoWeatherInSummary) { 'false' } else { 'true' }
$archiveResultsArg = if ($NoArchive) { 'false' } else { 'true' }

$arguments = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$DispatchScript`"",
    "-Repo", "`"$Repo`"",
    "-Workflow", "`"$Workflow`"",
    "-Ref", "`"$Ref`"",
    "-GhExe", "`"$GhExe`"",
    "-SendSummary", $sendSummaryArg,
    "-SendPodcast", $sendPodcastArg,
    "-SendImage", $sendImageArg,
    "-IncludeWeatherInSummary", $includeWeatherArg,
    "-ArchiveResults", $archiveResultsArg
)
if (![string]::IsNullOrWhiteSpace($TargetDate)) {
    $arguments += @("-TargetDate", "`"$TargetDate`"")
}
if (![string]::IsNullOrWhiteSpace($TargetStartDate)) {
    $arguments += @("-TargetStartDate", "`"$TargetStartDate`"")
}
if (![string]::IsNullOrWhiteSpace($TargetChatroom)) {
    $arguments += @("-TargetChatroom", "`"$TargetChatroom`"")
}
if ($SkipNonBusinessDays) {
    $arguments += @("-SkipNonBusinessDays", "true")
}

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
    -Description "Dispatches the qwerty agency Kakao GitHub Actions workflow every $IntervalMinutes minutes." `
    -Force | Out-Null

Write-Host "Registered task: $TaskName"
Write-Host "Interval minutes: $IntervalMinutes"
Write-Host "First scheduled run: $startAt"
Write-Host "Workflow target: $Repo / $Workflow"

if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Requested immediate run: $TaskName"
}
