param(
    [Parameter(Mandatory = $true)][ValidatePattern('^[A-Za-z0-9_-]+$')][string]$BundleId,
    [string]$ProjectRoot = "",
    [string]$PythonExe = "C:\Users\April\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
)

$ErrorActionPreference = "Stop"
if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = Split-Path -Parent $PSScriptRoot
}
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$env:PYTHONUTF8 = "1"
$env:QWERTY_BRIEFING_PROGRESS = "0"
if (!(Test-Path -LiteralPath $PythonExe)) {
    throw "Python was not found: $PythonExe"
}
$mcpCommand = Join-Path (Split-Path -Parent $PythonExe) "Scripts\kakaotalk-mcp.exe"
$arguments = @(
    (Join-Path $ProjectRoot "scripts\manage_briefing_delivery.py"), "send",
    "--bundle-id", $BundleId
)
if (Test-Path -LiteralPath $mcpCommand) {
    $arguments += @("--mcp-command", $mcpCommand)
}
$resultJson = & $PythonExe @arguments
$deliveryExitCode = $LASTEXITCODE
if ($deliveryExitCode -ne 0) {
    try {
        $result = $resultJson | ConvertFrom-Json
        Write-Output $result.last_error
    } catch {
        Write-Output "Saved briefing delivery did not complete. Check the pending items in the control app."
    }
} else {
    Write-Output "Saved briefing delivery completed."
}
exit $deliveryExitCode
