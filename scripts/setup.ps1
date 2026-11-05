# One-shot installer: Python venv, packages, overlay, offline models, Ollama + LLM.
# Run:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "`n[1/5] Python 3.12 environment" -ForegroundColor Cyan
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    py -3.12 -m venv .venv
    if (-not $?) { throw "Python 3.12 is required (winget install Python.Python.3.12)" }
}
.\.venv\Scripts\python.exe -m pip install --upgrade pip -q
.\.venv\Scripts\python.exe -m pip install -q -r backend\requirements.txt

Write-Host "`n[2/5] Overlay (Electron) and WhatsApp bridge" -ForegroundColor Cyan
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw "Node.js is required (winget install OpenJS.NodeJS.LTS), then open a new terminal" }
Push-Location ui; npm install --no-audit --no-fund; Pop-Location
Push-Location whatsapp-bridge; npm install --no-audit --no-fund; Pop-Location

Write-Host "`n[3/5] Offline models (wake word, speech, voices)" -ForegroundColor Cyan
.\.venv\Scripts\python.exe scripts\download_models.py

Write-Host "`n[4/5] Ollama (local language model)" -ForegroundColor Cyan
$ollama = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
if (-not (Test-Path $ollama)) { winget install --id Ollama.Ollama -e --silent --accept-package-agreements --accept-source-agreements }
$model = (Select-String -Path config.yaml -Pattern '^\s+model:\s*(qwen\S+|llama\S+|phi\S+|gemma\S+)' | Select-Object -First 1).Matches.Groups[1].Value
if (-not $model) { $model = "qwen2.5:3b" }
& $ollama pull $model

Write-Host "`n[5/5] Self-test" -ForegroundColor Cyan
Push-Location backend; ..\.venv\Scripts\python.exe -m jarvis --selftest; Pop-Location

Write-Host "`nDone. Start Jarvis with start_jarvis.bat (or scripts\install_startup.ps1 to start at login)." -ForegroundColor Green
