Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

scriptRoot = fso.GetParentFolderName(WScript.ScriptFullName)
scriptPath = scriptRoot & "\start_github_runner_hidden.ps1"

command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File " & Chr(34) & scriptPath & Chr(34)

shell.CurrentDirectory = scriptRoot
exitCode = shell.Run(command, 0, True)
WScript.Quit exitCode
