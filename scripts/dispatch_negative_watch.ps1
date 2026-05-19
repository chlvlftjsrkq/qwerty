param(
    [string]$Repo = "chlvlftjsrkq/qwerty",
    [string]$Workflow = "negative-news-watch.yml",
    [string]$Ref = "main",
    [string]$GhExe = "C:\Program Files\GitHub CLI\gh.exe",
    [string]$TargetChatroom = "",
    [int]$MaxAlerts = 1,
    [int]$LookbackHours = 168,
    [int]$TopicTtlHours = 12,
    [int]$RelatedHours = 12,
    [int]$RelatedLimit = 5,
    [int]$ActiveStartHour = 8,
    [int]$ActiveEndHour = 22,
    [string]$StateKey = "main",
    [string]$DryRun = "false",
    [string]$TriggerSource = "pc-negative-watch-main"
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

$Root = Split-Path -Parent $PSScriptRoot
if (!(Test-Path -LiteralPath $GhExe)) {
    throw "GitHub CLI was not found: $GhExe"
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
    "--field", "trigger_source=$TriggerSource"
)

Set-Location $Root
Write-Host "Dispatching $Workflow for room '$TargetChatroom' in $Repo..."
& $GhExe @argsList
if ($LASTEXITCODE -ne 0) {
    throw "gh workflow run failed with exit code $LASTEXITCODE"
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
    dry_run = (Convert-ToWorkflowBool $DryRun)
    trigger_source = $TriggerSource
    workflow = $Workflow
    repo = $Repo
}
Add-Content -LiteralPath $logPath -Value ($logObject | ConvertTo-Json -Compress) -Encoding UTF8

Write-Host "Dispatch requested successfully: room=$TargetChatroom, source=$TriggerSource"
