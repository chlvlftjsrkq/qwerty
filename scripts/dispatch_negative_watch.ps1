param(
    [string]$Repo = "chlvlftjsrkq/qwerty",
    [string]$Workflow = "negative-news-watch.yml",
    [string]$Ref = "main",
    [string]$GhExe = "C:\Program Files\GitHub CLI\gh.exe",
    [string]$TargetChatroom = "",
    [int]$MaxAlerts = 1,
    [int]$LookbackHours = 24,
    [int]$TopicTtlHours = 12,
    [int]$RelatedHours = 12,
    [int]$RelatedLimit = 5,
    [int]$ActiveStartHour = 8,
    [int]$ActiveEndHour = 22,
    [string]$StateKey = "main",
    [string]$DiagnosticChatroom = "",
    [string]$DryRun = "false",
    [string]$SendDiagnostic = "false",
    [string]$TriggerSource = "pc-negative-watch-main",
    [string]$PythonExe = "C:\Users\April\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe",
    [string]$McpCommand = "C:\Users\April\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\Scripts\kakaotalk-mcp.exe",
    [string]$CodexCommand = "C:\Users\April\AppData\Roaming\npm\codex.cmd",
    [string]$FallbackToLocal = "true"
)

$ErrorActionPreference = "Stop"

function Get-DefaultTargetChatroom {
    $codes = @(0x0041, 0x0049, 0x0020, 0xBCD1, 0xBB34, 0xCCAD, 0x0020, 0xB370, 0xC77C, 0xB9AC, 0x0020, 0xBAA8, 0xB2DD, 0xD1A1)
    return -join ($codes | ForEach-Object { [char]$_ })
}

function Convert-ToWorkflowBool {
    param([string]$Value)
    $normalized = "$Value".Trim().ToLowerInvariant()
    if ($normalized -in @("1", "true", "yes", "y", "on")) {
        return "true"
    }
    return "false"
}

function Convert-ToBool {
    param([string]$Value)
    $normalized = "$Value".Trim().ToLowerInvariant()
    return $normalized -in @("1", "true", "yes", "y", "on")
}

function Write-DispatchLog {
    param(
        [string]$LogPath,
        [hashtable]$LogObject
    )
    Add-Content -LiteralPath $LogPath -Value ($LogObject | ConvertTo-Json -Compress) -Encoding UTF8
}

$Root = Split-Path -Parent $PSScriptRoot
if (!(Test-Path -LiteralPath $GhExe)) {
    throw "GitHub CLI was not found: $GhExe"
}
if (!(Test-Path -LiteralPath $PythonExe)) {
    throw "Python executable was not found: $PythonExe"
}
if ([string]::IsNullOrWhiteSpace($TargetChatroom)) {
    $TargetChatroom = Get-DefaultTargetChatroom
}

$argsList = @(
    "workflow", "run", $Workflow,
    "--repo", $Repo,
    "--ref", $Ref,
    "--field", "target_chatroom=$TargetChatroom",
    "--field", "max_alerts=$MaxAlerts",
    "--field", "lookback_hours=$LookbackHours",
    "--field", "topic_ttl_hours=$TopicTtlHours",
    "--field", "related_hours=$RelatedHours",
    "--field", "related_limit=$RelatedLimit",
    "--field", "active_start_hour=$ActiveStartHour",
    "--field", "active_end_hour=$ActiveEndHour",
    "--field", "state_key=$StateKey",
    "--field", "dry_run=$(Convert-ToWorkflowBool $DryRun)",
    "--field", "send_diagnostic_report=$(Convert-ToWorkflowBool $SendDiagnostic)",
    "--field", "trigger_source=$TriggerSource"
)
if (![string]::IsNullOrWhiteSpace($DiagnosticChatroom)) {
    $argsList += @("--field", "diagnostic_chatroom=$DiagnosticChatroom")
}

Set-Location $Root
Write-Host "Dispatching $Workflow for room '$TargetChatroom' in $Repo..."
& $GhExe @argsList
if ($LASTEXITCODE -ne 0) {
    $ghExitCode = $LASTEXITCODE
    $stateDir = Join-Path $Root ".scheduler"
    New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
    $logPath = Join-Path $stateDir "negative-watch-dispatch-log.jsonl"
    Write-DispatchLog -LogPath $logPath -LogObject ([ordered]@{
        dispatched_at = (Get-Date).ToString("o")
        target_chatroom = $TargetChatroom
        max_alerts = $MaxAlerts
        lookback_hours = $LookbackHours
        topic_ttl_hours = $TopicTtlHours
        related_hours = $RelatedHours
        related_limit = $RelatedLimit
        active_start_hour = $ActiveStartHour
        active_end_hour = $ActiveEndHour
        state_key = $StateKey
        diagnostic_chatroom = $DiagnosticChatroom
        dry_run = (Convert-ToWorkflowBool $DryRun)
        send_diagnostic_report = (Convert-ToWorkflowBool $SendDiagnostic)
        trigger_source = $TriggerSource
        workflow = $Workflow
        repo = $Repo
        dispatch_failed = $true
        dispatch_exit_code = $ghExitCode
        fallback_to_local = (Convert-ToWorkflowBool $FallbackToLocal)
    })

    if (-not (Convert-ToBool $FallbackToLocal)) {
        throw "gh workflow run failed with exit code $ghExitCode"
    }

    Write-Host "gh workflow run failed with exit code $ghExitCode. Running local negative watch fallback..."
    $watchScript = Join-Path $Root "scripts\watch_negative_news.py"
    if (!(Test-Path -LiteralPath $watchScript)) {
        throw "Local fallback script was not found: $watchScript"
    }

    $statePath = Join-Path $stateDir "negative-news-watch-$StateKey-seen.json"
    $outputDir = Join-Path $Root "runs\negative-watch-$StateKey"
    $localArgs = @(
        $watchScript,
        "--room", $TargetChatroom,
        "--state", $statePath,
        "--output-dir", $outputDir,
        "--generate-alert-image",
        "--max-alerts", "$MaxAlerts",
        "--lookback-hours", "$LookbackHours",
        "--topic-ttl-hours", "$TopicTtlHours",
        "--related-hours", "$RelatedHours",
        "--related-limit", "$RelatedLimit",
        "--active-start-hour", "$ActiveStartHour",
        "--active-end-hour", "$ActiveEndHour"
    )
    if (Test-Path -LiteralPath $McpCommand) {
        $localArgs += @("--mcp-command", $McpCommand)
    }
    if (Test-Path -LiteralPath $CodexCommand) {
        $localArgs += @("--codex-command", $CodexCommand)
    }
    if (![string]::IsNullOrWhiteSpace($DiagnosticChatroom)) {
        $localArgs += @("--diagnostic-room", $DiagnosticChatroom)
    }
    if (Convert-ToBool $DryRun) {
        $localArgs += "--dry-run"
    }
    if (Convert-ToBool $SendDiagnostic) {
        $localArgs += "--send-diagnostic-report"
    }

    & $PythonExe @localArgs
    $fallbackExitCode = $LASTEXITCODE
    Write-DispatchLog -LogPath $logPath -LogObject ([ordered]@{
        dispatched_at = (Get-Date).ToString("o")
        target_chatroom = $TargetChatroom
        state_key = $StateKey
        diagnostic_chatroom = $DiagnosticChatroom
        trigger_source = $TriggerSource
        workflow = $Workflow
        repo = $Repo
        local_fallback = $true
        local_fallback_exit_code = $fallbackExitCode
    })
    if ($fallbackExitCode -ne 0) {
        throw "local negative watch fallback failed with exit code $fallbackExitCode"
    }
    Write-Host "Local negative watch fallback completed successfully."
    exit 0
}

$stateDir = Join-Path $Root ".scheduler"
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
$logPath = Join-Path $stateDir "negative-watch-dispatch-log.jsonl"
$logObject = [ordered]@{
    dispatched_at = (Get-Date).ToString("o")
    target_chatroom = $TargetChatroom
    max_alerts = $MaxAlerts
    lookback_hours = $LookbackHours
    topic_ttl_hours = $TopicTtlHours
    related_hours = $RelatedHours
    related_limit = $RelatedLimit
    active_start_hour = $ActiveStartHour
    active_end_hour = $ActiveEndHour
    state_key = $StateKey
    diagnostic_chatroom = $DiagnosticChatroom
    dry_run = (Convert-ToWorkflowBool $DryRun)
    send_diagnostic_report = (Convert-ToWorkflowBool $SendDiagnostic)
    trigger_source = $TriggerSource
    workflow = $Workflow
    repo = $Repo
    dispatch_failed = $false
}
Write-DispatchLog -LogPath $logPath -LogObject $logObject

Write-Host "Dispatch requested successfully: room=$TargetChatroom, source=$TriggerSource"
