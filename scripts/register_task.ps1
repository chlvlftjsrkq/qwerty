param(
    [string]$Time = "08:00",
    [string]$TaskName = "MMA Kakao Daily News"
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"

if (!(Test-Path $Python)) {
    throw "가상환경 Python을 찾지 못했습니다. 먼저 README의 설치 명령을 실행하세요: $Python"
}

$Action = New-ScheduledTaskAction `
    -Execute $Python `
    -Argument "-m kakao_mma_news --post --env-file `"$Root\.env`"" `
    -WorkingDirectory $Root

$Trigger = New-ScheduledTaskTrigger -Daily -At $Time
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "전날 병무청 관련 뉴스 요약을 PC 카카오톡 단톡방에 게시" `
    -Force

Write-Host "등록 완료: $TaskName / 매일 $Time"
