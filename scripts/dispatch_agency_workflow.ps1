param(
    [string]$Repo = "chlvlftjsrkq/qwerty",
    [string]$Workflow = "daily-post-mcp-self-hosted.yml",
    [string]$Ref = "main",
    [string]$GhExe = "C:\Program Files\GitHub CLI\gh.exe",
    [string]$StatePath = "",
    [string]$TargetDate = "",
    [string]$Agency = "",
    [int]$AgencyIndex = -1,
    [string]$SendSummary = "true",
    [string]$SendPodcast = "true",
    [string]$IncludeWeatherInSummary = "true",
    [string]$ArchiveResults = "true"
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
if ([string]::IsNullOrWhiteSpace($StatePath)) {
    $StatePath = Join-Path $Root ".scheduler\agency-dispatch-state.json"
}

if (!(Test-Path -LiteralPath $GhExe)) {
    throw "GitHub CLI was not found: $GhExe"
}

$schedulePath = Join-Path $Root "config\agency_schedule.json"
$schedule = Get-Content -LiteralPath $schedulePath -Raw -Encoding UTF8 | ConvertFrom-Json
$agencies = @($schedule.agencies)
if ($agencies.Count -eq 0) {
    throw "Agency schedule is empty: $schedulePath"
}

$selectedIndex = -1
if (![string]::IsNullOrWhiteSpace($Agency)) {
    for ($i = 0; $i -lt $agencies.Count; $i++) {
        if ($agencies[$i].agency -eq $Agency) {
            $selectedIndex = $i
            break
        }
    }
    if ($selectedIndex -lt 0) {
        throw "Agency was not found: $Agency"
    }
} elseif ($AgencyIndex -ge 0) {
    $selectedIndex = (($AgencyIndex % $agencies.Count) + $agencies.Count) % $agencies.Count
} else {
    $stateDir = Split-Path -Parent $StatePath
    New-Item -ItemType Directory -Force -Path $stateDir | Out-Null

    $lastIndex = -1
    if (Test-Path -LiteralPath $StatePath) {
        try {
            $state = Get-Content -LiteralPath $StatePath -Raw -Encoding UTF8 | ConvertFrom-Json
            $lastIndex = [int]$state.last_agency_index
        } catch {
            $lastIndex = -1
        }
    }
    $selectedIndex = ($lastIndex + 1) % $agencies.Count
}

$selected = $agencies[$selectedIndex]
$agencyName = [string]$selected.agency

$argsList = @(
    "workflow", "run", $Workflow,
    "--repo", $Repo,
    "--ref", $Ref,
    "--field", "agency_index=$selectedIndex",
    "--field", "agency=$agencyName",
    "--field", "send_summary=$(Convert-ToWorkflowBool $SendSummary)",
    "--field", "send_podcast=$(Convert-ToWorkflowBool $SendPodcast)",
    "--field", "include_weather_in_summary=$(Convert-ToWorkflowBool $IncludeWeatherInSummary)",
    "--field", "archive_results=$(Convert-ToWorkflowBool $ArchiveResults)"
)
if (![string]::IsNullOrWhiteSpace($TargetDate)) {
    $argsList += @("--field", "target_date=$TargetDate")
}

Set-Location $Root
Write-Host "Dispatching $Workflow for $agencyName (#$selectedIndex) in $Repo..."
& $GhExe @argsList
if ($LASTEXITCODE -ne 0) {
    throw "gh workflow run failed with exit code $LASTEXITCODE"
}

$stateDir = Split-Path -Parent $StatePath
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
$stateObject = [ordered]@{
    last_agency_index = $selectedIndex
    last_agency = $agencyName
    last_dispatch_at = (Get-Date).ToString("o")
    workflow = $Workflow
    repo = $Repo
}
$stateObject | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $StatePath -Encoding UTF8

$logPath = Join-Path $stateDir "agency-dispatch-log.jsonl"
$logObject = [ordered]@{
    dispatched_at = (Get-Date).ToString("o")
    agency_index = $selectedIndex
    agency = $agencyName
    target_date = $TargetDate
    workflow = $Workflow
    repo = $Repo
}
Add-Content -LiteralPath $logPath -Value ($logObject | ConvertTo-Json -Compress) -Encoding UTF8

Write-Host "Dispatch requested successfully: $agencyName (#$selectedIndex)"
