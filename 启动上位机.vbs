' 双击无窗口启动上位机（优先 Anaconda pythonw，避免 PATH 中无依赖的 pythonw 静默闪退）
Set fso = CreateObject("Scripting.FileSystemObject")
Set ws = CreateObject("Wscript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = "E:\Anaconda\pythonw.exe"
If Not fso.FileExists(pyw) Then pyw = "pythonw"
ws.Run """" & pyw & """ """ & dir & "\main.py""", 0, False
