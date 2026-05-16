$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"

if (!(Test-Path $Python)) {
    throw "가상환경 Python을 찾지 못했습니다: $Python"
}

Set-Location $Root
& $Python -m kakao_mma_news --post --env-file (Join-Path $Root ".env")
