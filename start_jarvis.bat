@echo off
rem Start JARVIS (backend + overlay). Add --selftest to check components, --no-ui for console mode.
cd /d "%~dp0backend"
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Jarvis is not installed yet. Run:  powershell -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1"
  pause
  exit /b 1
)
if "%~1"=="" (
  start "" "%~dp0.venv\Scripts\pythonw.exe" -m jarvis
) else (
  "%~dp0.venv\Scripts\python.exe" -m jarvis %*
  if "%~1"=="--selftest" pause
)
