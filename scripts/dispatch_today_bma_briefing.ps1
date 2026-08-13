param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$Repo = "chlvlftjsrkq/qwerty",
    [string]$Workflow = "daily-post-mcp-self-hosted.yml",
    [string]$Ref = "main",
    [string]$GhExe = "C:\Program Files\GitHub CLI\gh.exe"
)

$ErrorActionPreference = "Stop"

$controlDirectory = Join-Path $env:LOCALAPPDATA "qwerty"
$deliveryPauseFile = Join-Path $controlDirectory "kakao-delivery.pause"
$runnerPauseFile = Join-Path $controlDirectory "github-actions-runner.pause"
$lockPath = Join-Path $controlDirectory "today-briefing-dispatch.lock"
$logPath = Join-Path $controlDirectory "today-briefing-dispatch.log"
New-Item -ItemType Directory -Path $controlDirectory -Force | Out-Null

if (Test-Path -LiteralPath $deliveryPauseFile) {
    throw "KakaoTalk delivery is off. Turn delivery on in the control app first."
}
if (Test-Path -LiteralPath $runnerPauseFile) {
    throw "The GitHub Actions runner is off. Turn the runner on in the control app first."
}
if (!(Test-Path -LiteralPath $GhExe)) {
    throw "GitHub CLI was not found: $GhExe"
}

$lockStream = $null
try {
    try {
        $lockStream = [System.IO.File]::Open(
            $lockPath,
            [System.IO.FileMode]::OpenOrCreate,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
    } catch {
        throw "A briefing dispatch request is already being processed."
    }

    $activeRunsJson = & $GhExe run list `
        --repo $Repo `
        --workflow $Workflow `
        --limit 10 `
        --json status,displayTitle,databaseId 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect GitHub workflow runs. $($activeRunsJson -join ' ')"
    }
    $activeRuns = @($activeRunsJson | ConvertFrom-Json) | Where-Object {
        $_.status -in @("queued", "in_progress", "waiting", "pending")
    }
    if ($activeRuns.Count -gt 0) {
        $runId = [string]$activeRuns[0].databaseId
        throw "A briefing workflow is already running. GitHub run: $runId"
    }

    $dispatchScript = Join-Path $ProjectRoot "scripts\dispatch_agency_workflow.ps1"
    if (!(Test-Path -LiteralPath $dispatchScript)) {
        throw "The briefing dispatch script was not found: $dispatchScript"
    }

    $today = (Get-Date).ToString("yyyy-MM-dd")
    $agencyName = -join (@(0xBCD1, 0xBB34, 0xCCAD) | ForEach-Object { [char]$_ })
    & $dispatchScript `
        -Repo $Repo `
        -Workflow $Workflow `
        -Ref $Ref `
        -GhExe $GhExe `
        -Agency $agencyName `
        -SendSummary "true" `
        -SendPodcast "true" `
        -SendImage "true" `
        -IncludeWeatherInSummary "true" `
        -ArchiveResults "true" `
        -SkipNonBusinessDays "true" `
        -BusinessDate $today `
        -TriggerSource "control-panel-today-briefing"
    if ($LASTEXITCODE -ne 0) {
        throw "The briefing dispatch request failed with exit code $LASTEXITCODE."
    }

    $message = "Today's briefing generation and delivery were requested."
    Add-Content -LiteralPath $logPath -Value "$(Get-Date -Format o) SUCCESS $message" -Encoding UTF8
    Write-Output $message
}
catch {
    Add-Content -LiteralPath $logPath -Value "$(Get-Date -Format o) ERROR $($_.Exception.Message)" -Encoding UTF8
    throw
}
finally {
    if ($null -ne $lockStream) {
        $lockStream.Dispose()
    }
}
