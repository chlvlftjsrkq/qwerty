param(
    [string]$TaskName = "Qwerty Negative MMA News Watch 5min Test",
    [int]$IntervalMinutes = 5,
    [string]$Repo = "chlvlftjsrkq/qwerty",
    [string]$Workflow = "negative-news-watch.yml",
    [string]$Ref = "main",
    [string]$GhExe = "C:\Program Files\GitHub CLI\gh.exe",
    [string]$TargetChatroom = "test",
    [int]$MaxAlerts = 1,
    [int]$LookbackHours = 168,
    [int]$TopicTtlHours = 12,
    [int]$RelatedHours = 12,
    [int]$RelatedLimit = 5,
    [int]$ActiveStartHour = 8,
    [int]$ActiveEndHour = 22,
    [switch]$DryRun,
    [switch]$StartNow
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$DispatchScript = Join-Path $Root "scripts\dispatch_negative_watch.ps1"
if (!(Test-Path -LiteralPath $DispatchScript)) {
    throw "Dispatch script not found: $DispatchScript"
}
if (!(Test-Path -LiteralPath $GhExe)) {
    throw "GitHub CLI not found: $GhExe"
}

$dryRunValue = if ($DryRun) { "true" } else { "false" }
$arguments = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$DispatchScript`"",
    "-Repo", "`"$Repo`"",
    "-Workflow", "`"$Workflow`"",
    "-Ref", "`"$Ref`"",
    "-GhExe", "`"$GhExe`"",
    "-TargetChatroom", "`"$TargetChatroom`"",
    "-MaxAlerts", "$MaxAlerts",
    "-LookbackHours", "$LookbackHours",
    "-TopicTtlHours", "$TopicTtlHours",
    "-RelatedHours", "$RelatedHours",
    "-RelatedLimit", "$RelatedLimit",
    "-ActiveStartHour", "$ActiveStartHour",
    "-ActiveEndHour", "$ActiveEndHour",
    "-DryRun", "$dryRunValue",
    "-TriggerSource", "pc-negative-watch-5min-test"
)

$startHour = (($ActiveStartHour % 24) + 24) % 24
$endHour = (($ActiveEndHour % 24) + 24) % 24
$durationHours = if ($endHour -gt $startHour) { $endHour - $startHour } elseif ($endHour -lt $startHour) { 24 - $startHour + $endHour } else { 24 }
$startAt = (Get-Date).Date.AddHours($startHour)

function Escape-Xml {
    param([string]$Value)
    return [System.Security.SecurityElement]::Escape($Value)
}

$userId = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$argumentText = $arguments -join " "
$startBoundary = $startAt.ToString("yyyy-MM-ddTHH:mm:ss")
$intervalIso = "PT$($IntervalMinutes)M"
$durationIso = "PT$($durationHours)H"
$description = "Dispatches the qwerty negative MMA news watch workflow every $IntervalMinutes minutes from $($startHour):00 to $($endHour):00."

$taskXml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>$(Escape-Xml $description)</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <Repetition>
        <Interval>$intervalIso</Interval>
        <Duration>$durationIso</Duration>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <StartBoundary>$startBoundary</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$(Escape-Xml $userId)</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>true</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>true</WakeToRun>
    <ExecutionTimeLimit>PT30M</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>powershell.exe</Command>
      <Arguments>$(Escape-Xml $argumentText)</Arguments>
      <WorkingDirectory>$(Escape-Xml $Root)</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

Register-ScheduledTask -TaskName $TaskName -Xml $taskXml -Force | Out-Null

Write-Host "Registered task: $TaskName"
Write-Host "Interval minutes: $IntervalMinutes"
Write-Host "Active hours: $($startHour):00-$($endHour):00"
Write-Host "Daily start: $startAt"
Write-Host "Target chatroom: $TargetChatroom"
Write-Host "Workflow target: $Repo / $Workflow"

if ($StartNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Requested immediate run: $TaskName"
}
