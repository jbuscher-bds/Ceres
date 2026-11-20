Set objShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
strDir = fso.GetParentFolderName(WScript.ScriptFullName)
objShell.CurrentDirectory = strDir

strArgs = ""
For Each arg In WScript.Arguments
    strArgs = strArgs & " """ & arg & """"
Next

objShell.Run """" & strDir & "\.venv\Scripts\pythonw.exe"" -m ceres" & strArgs, 0, False
