param(
    [string]$Repo = "chlvlftjsrkq/qwerty",
    [string]$Workflow = "negative-news-watch.yml",
    [string]$Ref = "main",
    [string]$GhExe = "C:\Program Files\GitHub CLI\gh.exe",
    [string]$TargetChatroom = "test",
    [int]$MaxAlerts = 1,
    [int]$LookbackHours = 168,
    [string]$DryRun = "false",
    [string]$TriggerSource = "pc-negative-watch-5min-test"
)

$ErrorActionPreference = "Stop"

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

$argsList = @(
    "workflow", "run", $Workflow,
    "--repo", $Repo,
    "--ref", $Ref,
    "--field", "target_chatroom=$TargetChatroom",
    "--field", "max_alerts=$MaxAlerts",
    "--field", "lookback_hours=$LookbackHours",
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
    dry_run = (Convert-ToWorkflowBool $DryRun)
    trigger_source = $TriggerSource
    workflow = $Workflow
    repo = $Repo
}
Add-Content -LiteralPath $logPath -Value ($logObject | ConvertTo-Json -Compress) -Encoding UTF8

Write-Host "Dispatch requested successfully: room=$TargetChatroom, source=$TriggerSource"
