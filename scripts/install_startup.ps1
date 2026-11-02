# Start JARVIS when you sign in (a shortcut in your Startup folder). Run again with -Remove to undo.
param([switch]$Remove)
$root = Split-Path -Parent $PSScriptRoot
$lnk = Join-Path ([Environment]::GetFolderPath("Startup")) "JARVIS.lnk"
if ($Remove) { Remove-Item $lnk -ErrorAction SilentlyContinue; Write-Host "Removed from startup."; exit }
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$s.TargetPath = Join-Path $root ".venv\Scripts\pythonw.exe"
$s.Arguments = "-m jarvis"
$s.WorkingDirectory = Join-Path $root "backend"
$s.Description = "JARVIS voice assistant"
$s.Save()
Write-Host "JARVIS will start when you sign in. ($lnk)"
