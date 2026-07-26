param(
    [string]$RunnerRoot = "C:\omx\actions-runner-qwerty",
    [string]$PauseFile = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($PauseFile)) {
    $PauseFile = if ([string]::IsNullOrWhiteSpace($env:GITHUB_RUNNER_PAUSE_FILE)) {
        Join-Path $env:LOCALAPPDATA "qwerty\github-actions-runner.pause"
    } else {
        [Environment]::ExpandEnvironmentVariables($env:GITHUB_RUNNER_PAUSE_FILE.Trim())
    }
}

function Test-RunnerListenerRunning {
    param([string]$Root)

    $escapedRoot = [WildcardPattern]::Escape($Root)
    $existing = Get-CimInstance Win32_Process -Filter "Name = 'Runner.Listener.exe'" |
        Where-Object { $_.CommandLine -like "*$escapedRoot*" } |
        Select-Object -First 1
    return $null -ne $existing
}

if (!(Test-Path -LiteralPath $RunnerRoot)) {
    throw "Runner root was not found: $RunnerRoot"
}

$logPath = Join-Path $RunnerRoot "runner-start.log"
if (Test-Path -LiteralPath $PauseFile) {
    $timestamp = (Get-Date).ToString("o")
    Add-Content -LiteralPath $logPath -Value "${timestamp}: Runner start skipped because the pause marker exists: $PauseFile" -Encoding UTF8
    exit 0
}

if (Test-RunnerListenerRunning -Root $RunnerRoot) {
    exit 0
}

$runCmd = Join-Path $RunnerRoot "run.cmd"
if (!(Test-Path -LiteralPath $runCmd)) {
    throw "Runner run.cmd was not found: $runCmd"
}


$timestamp = (Get-Date).ToString("o")
Add-Content -LiteralPath $logPath -Value "${timestamp}: Starting hidden GitHub Actions runner." -Encoding UTF8

$psi = [System.Diagnostics.ProcessStartInfo]::new()
$psi.FileName = "$env:ComSpec"
$psi.Arguments = "/d /s /c `"`"$runCmd`" >> `"$logPath`" 2>>&1`""
$psi.WorkingDirectory = $RunnerRoot
$psi.UseShellExecute = $true
$psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden

try {
    $process = [System.Diagnostics.Process]::Start($psi)
    $process.WaitForExit()
    exit $process.ExitCode
} finally {
    if ($null -ne $process) {
        $process.Dispose()
    }
}
