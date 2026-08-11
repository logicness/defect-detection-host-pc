' 双击无窗口启动上位机
Set fso = CreateObject("Scripting.FileSystemObject")
Set ws = CreateObject("Wscript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
ws.Run "pythonw """ & dir & "\main.py""", 0, False
