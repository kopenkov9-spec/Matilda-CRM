Option Explicit
Dim shell, files, root, command
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
root = files.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = root
command = Chr(34) & root & "\.venv\Scripts\pythonw.exe" & Chr(34) & " -B " & Chr(34) & root & "\launcher.py" & Chr(34)
If WScript.Arguments.Count > 0 Then
  If WScript.Arguments(0) = "--https" Then command = command & " --https"
End If
shell.Run command, 0, False
