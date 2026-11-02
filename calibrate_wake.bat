@echo off
rem Tune "Hey Jarvis" and the mic level to your voice (about a minute), then restart Jarvis.
cd /d "%~dp0backend"
echo Pausing Jarvis during calibration...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*-m jarvis*' -and $_.CommandLine -notlike '*--calibrate*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; Get-Process electron -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*jarvis*' } | Stop-Process -Force" >nul 2>&1
"%~dp0.venv\Scripts\python.exe" -m jarvis --calibrate
echo Starting Jarvis with the new settings...
start "" "%~dp0.venv\Scripts\pythonw.exe" -m jarvis
timeout /t 4 >nul
