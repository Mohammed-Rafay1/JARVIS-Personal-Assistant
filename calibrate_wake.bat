@echo off
rem Tune Jarvis to your voice (8-10 minutes: your voiceprint, a quiet room, then background noise, then other
rem people talking). "calibrate_wake.bat quick" is the 3-minute version (quiet room, voiceprint, other voices).
rem Jarvis is paused meanwhile and restarts with the new settings.
cd /d "%~dp0backend"
echo Pausing Jarvis during calibration...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { ($_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -like '*-m jarvis*' -and $_.CommandLine -notlike '*--calibrate*') -or ($_.Name -eq 'electron.exe' -and $_.CommandLine -like '*jarvis*') -or ($_.Name -eq 'node.exe' -and $_.CommandLine -like '*whatsapp-bridge*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1
if /i "%~1"=="quick" (
  "%~dp0.venv\Scripts\python.exe" -m jarvis --calibrate --quick
) else (
  "%~dp0.venv\Scripts\python.exe" -m jarvis --calibrate
)
echo Starting Jarvis with the new settings...
start "" "%~dp0.venv\Scripts\pythonw.exe" -m jarvis
timeout /t 4 >nul
