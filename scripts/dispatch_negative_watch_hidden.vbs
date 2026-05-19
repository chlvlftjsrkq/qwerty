Set shell = CreateObject("WScript.Shell")
root = "C:\omx\qwerty"
scriptPath = root & "\scripts\dispatch_negative_watch.ps1"
ghPath = "C:\Program Files\GitHub CLI\gh.exe"

command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File " & Chr(34) & scriptPath & Chr(34) _
  & " -Repo " & Chr(34) & "chlvlftjsrkq/qwerty" & Chr(34) _
  & " -Workflow " & Chr(34) & "negative-news-watch.yml" & Chr(34) _
  & " -Ref " & Chr(34) & "main" & Chr(34) _
  & " -GhExe " & Chr(34) & ghPath & Chr(34) _
  & " -MaxAlerts 1" _
  & " -LookbackHours 168" _
  & " -TopicTtlHours 12" _
  & " -RelatedHours 12" _
  & " -RelatedLimit 5" _
  & " -ActiveStartHour 8" _
  & " -ActiveEndHour 22" _
  & " -DryRun false" _
  & " -TriggerSource pc-negative-watch-main"

shell.CurrentDirectory = root
shell.Run command, 0, False
