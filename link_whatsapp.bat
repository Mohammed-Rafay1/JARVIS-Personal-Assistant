@echo off
rem Link Jarvis to WhatsApp again (after unlinking it on your phone, or to use another number).
rem Forgets the old link, restarts Jarvis, and opens the QR code page to scan.
cd /d "%~dp0"
if not exist "%~dp0whatsapp-bridge\node_modules" (
  pushd "%~dp0whatsapp-bridge" && call npm install --no-audit --no-fund && popd
)
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='pythonw.exe' OR Name='node.exe'\" | Where-Object { $_.CommandLine -like '*-m jarvis*' -or $_.CommandLine -like '*whatsapp-bridge*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; Get-Process electron -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*jarvis*' } | Stop-Process -Force" >nul 2>&1
if exist "%~dp0data\whatsapp-auth" rmdir /s /q "%~dp0data\whatsapp-auth"
if exist "%~dp0data\whatsapp-link.html" del "%~dp0data\whatsapp-link.html"
cd /d "%~dp0backend"
start "" "%~dp0.venv\Scripts\pythonw.exe" -m jarvis
echo Jarvis is restarting. The QR code page opens in your browser in about 30 seconds.
timeout /t 6 >nul
