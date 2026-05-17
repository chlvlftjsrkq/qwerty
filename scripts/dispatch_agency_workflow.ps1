param(
    [string]$Repo = "chlvlftjsrkq/qwerty",
    [string]$Workflow = "daily-post-mcp-self-hosted.yml",
    [string]$Ref = "main",
    [string]$GhExe = "C:\Program Files\GitHub CLI\gh.exe",
    [string]$StatePath = "",
    [string]$TargetDate = "",
    [string]$Agency = "",
    [int]$AgencyIndex = -1,
    [bool]$SendSummary = $true,
    [bool]$SendPodcast = $true,
    [bool]$IncludeWeatherInSummary = $true,
    [bool]$ArchiveResults = $true
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($StatePath)) {
    $StatePath = Join-Path $Root ".scheduler\agency-dispatch-state.json"
}

if (!(Test-Path -LiteralPath $GhExe)) {
    throw "GitHub CLI를 찾지 못했습니다: $GhExe"
}

$schedulePath = Join-Path $Root "config\agency_schedule.json"
$schedule = Get-Content -LiteralPath $schedulePath -Raw -Encoding UTF8 | ConvertFrom-Json
$agencies = @($schedule.agencies)
if ($agencies.Count -eq 0) {
    throw "기관 목록이 비어 있습니다: $schedulePath"
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
        throw "기관명을 찾지 못했습니다: $Agency"
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
    "--field", "send_summary=$($SendSummary.ToString().ToLowerInvariant())",
    "--field", "send_podcast=$($SendPodcast.ToString().ToLowerInvariant())",
    "--field", "include_weather_in_summary=$($IncludeWeatherInSummary.ToString().ToLowerInvariant())",
    "--field", "archive_results=$($ArchiveResults.ToString().ToLowerInvariant())"
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
