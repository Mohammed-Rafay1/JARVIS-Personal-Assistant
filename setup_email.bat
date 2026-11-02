@echo off
rem Connect Gmail with an app password (no Google Cloud project), then restart Jarvis.
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" scripts\email_setup.py
if errorlevel 1 (
  pause
  exit /b 1
)
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*-m jarvis*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; Get-Process electron -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*jarvis*' } | Stop-Process -Force" >nul 2>&1
cd /d "%~dp0backend"
start "" "%~dp0.venv\Scripts\pythonw.exe" -m jarvis
timeout /t 4 >nul
