param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [switch]$SkipDesktopShortcut
)

$ErrorActionPreference = "Stop"

$sourcePath = Join-Path $ProjectRoot "tools\KakaoDeliveryControl.cs"
if (!(Test-Path -LiteralPath $sourcePath)) {
    throw "Kakao delivery control source was not found: $sourcePath"
}

$installDirectory = Join-Path $env:LOCALAPPDATA "qwerty"
$targetPath = Join-Path $installDirectory "KakaoDeliveryControl.exe"
$temporaryPath = Join-Path $env:TEMP "KakaoDeliveryControl-$PID.exe"
New-Item -ItemType Directory -Path $installDirectory -Force | Out-Null

$compilerCandidates = @(
    (Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"),
    (Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe")
)
$compiler = $compilerCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (!$compiler) {
    throw "Windows C# compiler was not found."
}

try {
    & $compiler /nologo /target:winexe /optimize+ /codepage:65001 /reference:System.dll /reference:System.Drawing.dll /reference:System.Windows.Forms.dll "/out:$temporaryPath" $sourcePath
    if ($LASTEXITCODE -ne 0 -or !(Test-Path -LiteralPath $temporaryPath)) {
        throw "Kakao delivery control build failed with exit code $LASTEXITCODE."
    }
    Move-Item -LiteralPath $temporaryPath -Destination $targetPath -Force
}
finally {
    Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
}

if (!$SkipDesktopShortcut) {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $shortcutName = (-join @(
        [char]0xCE74, [char]0xCE74, [char]0xC624, [char]0xD1A1, [char]0x20,
        [char]0xC790, [char]0xB3D9, [char]0xBC1C, [char]0xC1A1, [char]0x20,
        [char]0xC81C, [char]0xC5B4
    )) + ".lnk"
    $shortcutPath = Join-Path $desktop $shortcutName
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $targetPath
    $shortcut.WorkingDirectory = $ProjectRoot
    $shortcut.Description = "Control qwerty KakaoTalk delivery."
    $shortcut.IconLocation = "$targetPath,0"
    $shortcut.Save()
}

[pscustomobject]@{
    Executable = $targetPath
    Shortcut = if ($SkipDesktopShortcut) { "" } else { $shortcutPath }
    PauseFile = Join-Path $installDirectory "kakao-delivery.pause"
}
