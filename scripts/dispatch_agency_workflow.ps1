param(
    [string]$Repo = "chlvlftjsrkq/qwerty",
    [string]$Workflow = "daily-post-mcp-self-hosted.yml",
    [string]$Ref = "main",
    [string]$GhExe = "C:\Program Files\GitHub CLI\gh.exe",
    [string]$StatePath = "",
    [string]$TargetStartDate = "",
    [string]$TargetDate = "",
    [string]$Agency = "",
    [int]$AgencyIndex = -1,
    [string]$SendSummary = "true",
    [string]$SendPodcast = "true",
    [string]$SendImage = "true",
    [string]$IncludeWeatherInSummary = "true",
    [string]$ArchiveResults = "true",
    [string]$TargetChatroom = "",
    [string]$TriggerSource = "pc-scheduler-0737",
    [string]$SkipNonBusinessDays = "false",
    [string]$BusinessDate = "",
    [string]$PythonExe = "C:\Users\April\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe",
    [switch]$DryRun
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

function Convert-ToBool {
    param([string]$Value)
    $normalized = "$Value".Trim().ToLowerInvariant()
    return $normalized -in @("1", "true", "yes", "y", "on")
}

function Write-DispatchLog {
    param(
        [string]$StatePath,
        [hashtable]$LogObject
    )
    $stateDir = Split-Path -Parent $StatePath
    New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
    $logPath = Join-Path $stateDir "agency-dispatch-log.jsonl"
    Add-Content -LiteralPath $logPath -Value ($LogObject | ConvertTo-Json -Compress) -Encoding UTF8
}

function Get-BusinessDayStatus {
    param(
        [string]$DateValue,
        [string]$PythonExe,
        [string]$BusinessDayScript
    )
    $businessStatusPath = Join-Path ([System.IO.Path]::GetTempPath()) ("qwerty-business-day-" + [guid]::NewGuid().ToString() + ".json")
    try {
        & $PythonExe $BusinessDayScript --date $DateValue --output $businessStatusPath | Out-Null
        $businessJson = Get-Content -LiteralPath $businessStatusPath -Raw -Encoding UTF8
        return $businessJson | ConvertFrom-Json
    } finally {
        if (Test-Path -LiteralPath $businessStatusPath) {
            Remove-Item -LiteralPath $businessStatusPath -Force
        }
    }
}

function Get-PreviousBusinessDate {
    param(
        [datetime]$BusinessDate,
        [string]$PythonExe,
        [string]$BusinessDayScript
    )
    $candidate = $BusinessDate.Date.AddDays(-1)
    while ($true) {
        $status = Get-BusinessDayStatus -DateValue $candidate.ToString("yyyy-MM-dd") -PythonExe $PythonExe -BusinessDayScript $BusinessDayScript
        if ([bool]$status.business_day) {
            return $candidate
        }
        $candidate = $candidate.AddDays(-1)
    }
}

$Root = Split-Path -Parent $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($StatePath)) {
    $StatePath = Join-Path $Root ".scheduler\agency-dispatch-state.json"
}

if (!(Test-Path -LiteralPath $GhExe)) {
    throw "GitHub CLI was not found: $GhExe"
}

if (Convert-ToBool $SkipNonBusinessDays) {
    if (!(Test-Path -LiteralPath $PythonExe)) {
        throw "Python executable was not found: $PythonExe"
    }
    $businessDayScript = Join-Path $Root "scripts\is_korean_business_day.py"
    if (!(Test-Path -LiteralPath $businessDayScript)) {
        throw "Business day script was not found: $businessDayScript"
    }
    $businessDateArg = $BusinessDate
    if ([string]::IsNullOrWhiteSpace($businessDateArg)) {
        $businessDateArg = (Get-Date).ToString("yyyy-MM-dd")
    }
    $businessStatus = Get-BusinessDayStatus -DateValue $businessDateArg -PythonExe $PythonExe -BusinessDayScript $businessDayScript
    if (-not [bool]$businessStatus.business_day) {
        $skipLog = @{
            dispatched_at = (Get-Date).ToString("o")
            agency_index = $AgencyIndex
            agency = $Agency
            target_start_date = $TargetStartDate
            target_date = $TargetDate
            trigger_source = $TriggerSource
            workflow = $Workflow
            repo = $Repo
            skipped = $true
            skip_reason = [string]$businessStatus.reason
            holiday_name = [string]$businessStatus.holiday_name
            business_date = [string]$businessStatus.date
        }
        Write-DispatchLog -StatePath $StatePath -LogObject $skipLog
        Write-Host "Skipped dispatch: $($businessStatus.date) is $($businessStatus.reason) $($businessStatus.holiday_name)"
        exit 0
    }

    if ([string]::IsNullOrWhiteSpace($TargetDate) -and [string]::IsNullOrWhiteSpace($TargetStartDate)) {
        $businessDateObject = [datetime]::ParseExact([string]$businessStatus.date, "yyyy-MM-dd", [Globalization.CultureInfo]::InvariantCulture)
        $targetEndObject = $businessDateObject.Date.AddDays(-1)
        $targetStartObject = Get-PreviousBusinessDate -BusinessDate $businessDateObject -PythonExe $PythonExe -BusinessDayScript $businessDayScript
        $TargetStartDate = $targetStartObject.ToString("yyyy-MM-dd")
        $TargetDate = $targetEndObject.ToString("yyyy-MM-dd")
        Write-Host "Computed combined briefing target range: $TargetStartDate to $TargetDate"
    }
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
    "--field", "trigger_source=$TriggerSource",
    "--field", "send_summary=$(Convert-ToWorkflowBool $SendSummary)",
    "--field", "send_podcast=$(Convert-ToWorkflowBool $SendPodcast)",
    "--field", "send_image=$(Convert-ToWorkflowBool $SendImage)",
    "--field", "include_weather_in_summary=$(Convert-ToWorkflowBool $IncludeWeatherInSummary)",
    "--field", "archive_results=$(Convert-ToWorkflowBool $ArchiveResults)"
)
if (![string]::IsNullOrWhiteSpace($TargetStartDate)) {
    $argsList += @("--field", "target_start_date=$TargetStartDate")
}
if (![string]::IsNullOrWhiteSpace($TargetDate)) {
    $argsList += @("--field", "target_date=$TargetDate")
}
if (![string]::IsNullOrWhiteSpace($TargetChatroom)) {
    $argsList += @("--field", "target_chatroom=$TargetChatroom")
}

Set-Location $Root
Write-Host "Dispatching $Workflow for $agencyName (#$selectedIndex) in $Repo..."
if ($DryRun) {
    Write-Host "Dry run only. GitHub CLI args: $($argsList -join ' ')"
    exit 0
}
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
    last_target_start_date = $TargetStartDate
    last_target_date = $TargetDate
    trigger_source = $TriggerSource
    workflow = $Workflow
    repo = $Repo
}
$stateObject | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $StatePath -Encoding UTF8

$logPath = Join-Path $stateDir "agency-dispatch-log.jsonl"
$logObject = [ordered]@{
    dispatched_at = (Get-Date).ToString("o")
    agency_index = $selectedIndex
    agency = $agencyName
    target_start_date = $TargetStartDate
    target_date = $TargetDate
    trigger_source = $TriggerSource
    workflow = $Workflow
    repo = $Repo
}
Add-Content -LiteralPath $logPath -Value ($logObject | ConvertTo-Json -Compress) -Encoding UTF8

Write-Host "Dispatch requested successfully: $agencyName (#$selectedIndex), source=$TriggerSource, range=$TargetStartDate to $TargetDate"
