@echo off
rem Tune "Hey Jarvis" and the mic to your voice (6-8 minutes: a quiet room, then with background noise;
rem low, normal and loud voice). Jarvis is paused meanwhile and restarts with the new settings.
cd /d "%~dp0backend"
echo Pausing Jarvis during calibration...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { ($_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -like '*-m jarvis*' -and $_.CommandLine -notlike '*--calibrate*') -or ($_.Name -eq 'electron.exe' -and $_.CommandLine -like '*jarvis*') -or ($_.Name -eq 'node.exe' -and $_.CommandLine -like '*whatsapp-bridge*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1
"%~dp0.venv\Scripts\python.exe" -m jarvis --calibrate
echo Starting Jarvis with the new settings...
start "" "%~dp0.venv\Scripts\pythonw.exe" -m jarvis
timeout /t 4 >nul
