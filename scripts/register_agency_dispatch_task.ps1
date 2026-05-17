param(
    [string]$TaskName = "Qwerty Agency Kakao Workflow Dispatch 5min",
    [int]$IntervalMinutes = 5,
    [string]$Repo = "chlvlftjsrkq/qwerty",
    [string]$Workflow = "daily-post-mcp-self-hosted.yml",
    [string]$Ref = "main",
    [string]$GhExe = "C:\Program Files\GitHub CLI\gh.exe",
    [string]$TargetDate = "",
    [switch]$NoSummary,
    [switch]$NoPodcast,
    [switch]$NoArchive,
    [switch]$NoWeatherInSummary,
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

$sendSummaryArg = if ($NoSummary) { '$false' } else { '$true' }
$sendPodcastArg = if ($NoPodcast) { '$false' } else { '$true' }
$includeWeatherArg = if ($NoWeatherInSummary) { '$false' } else { '$true' }
$archiveResultsArg = if ($NoArchive) { '$false' } else { '$true' }

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
    "-IncludeWeatherInSummary", $includeWeatherArg,
    "-ArchiveResults", $archiveResultsArg
)
if (![string]::IsNullOrWhiteSpace($TargetDate)) {
    $arguments += @("-TargetDate", "`"$TargetDate`"")
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

Write-Host "등록 완료: $TaskName"
Write-Host "반복 간격: $IntervalMinutes분"
Write-Host "첫 실행 예정: $startAt"
Write-Host "대상 workflow: $Repo / $Workflow"

if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "즉시 실행을 요청했습니다: $TaskName"
}
