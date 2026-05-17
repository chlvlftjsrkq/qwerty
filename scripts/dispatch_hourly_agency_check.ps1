param(
    [string]$Repo = "chlvlftjsrkq/qwerty",
    [string]$Workflow = "hourly-agency-schedule-check.yml",
    [string]$Ref = "main"
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $Root "runs\dispatch-logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Now = Get-Date
$LogPath = Join-Path $LogDir ("agency-dispatch-{0}.log" -f $Now.ToString("yyyyMMdd-HHmmss"))

function Write-DispatchLog {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date).ToString("yyyy-MM-dd HH:mm:ss zzz"), $Message
    Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
    Write-Host $line
}

$GhCandidates = @(
    "C:\Program Files\GitHub CLI\gh.exe",
    "C:\Program Files (x86)\GitHub CLI\gh.exe",
    "gh.exe"
)

$Gh = $null
foreach ($Candidate in $GhCandidates) {
    $Resolved = Get-Command $Candidate -ErrorAction SilentlyContinue
    if ($Resolved) {
        $Gh = $Resolved.Source
        break
    }
}

if (-not $Gh) {
    throw "GitHub CLI gh.exe was not found."
}

Write-DispatchLog "Dispatching $Workflow for $Repo on ref $Ref"
Push-Location $Root
try {
    $output = & $Gh workflow run $Workflow --repo $Repo --ref $Ref 2>&1
    $exitCode = $LASTEXITCODE
    if ($output) {
        $output | ForEach-Object { Write-DispatchLog $_ }
    }
    if ($exitCode -ne 0) {
        throw "gh workflow run failed with exit code $exitCode"
    }
    Write-DispatchLog "Dispatch complete"
} finally {
    Pop-Location
}
