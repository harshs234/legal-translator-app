# =====================================================================
#  install_services_nssm.ps1
#  Installs the translator service + n8n as true Windows SERVICES using
#  NSSM. More robust than Scheduled Tasks: auto-restarts on crash, runs
#  before login, survives reboot. REQUIRES ADMIN + nssm.exe on PATH.
#
#  Get NSSM:  https://nssm.cc/download  (unzip, put nssm.exe on PATH)
#  Run from an ADMIN PowerShell, inside automation\ :
#      powershell -ExecutionPolicy Bypass -File .\install_services_nssm.ps1
#
#  To remove:  nssm remove MutarjimTranslator confirm
#              nssm remove MutarjimN8n confirm
# =====================================================================

$ErrorActionPreference = "Stop"
$here       = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectDir = Split-Path -Parent $here          # project root = one level up
$venvName   = "venv313"
$python     = Join-Path $projectDir "$venvName\Scripts\python.exe"
$port       = 8000

if (-not (Get-Command nssm -ErrorAction SilentlyContinue)) {
    throw "nssm.exe not found on PATH. Download from https://nssm.cc/download first."
}
if (-not (Test-Path $python)) {
    throw "Python not found at $python — check VENV_NAME."
}

# --- Translator service ---------------------------------------------
nssm install MutarjimTranslator $python `
    "-m" "uvicorn" "translator_service:app" "--host" "0.0.0.0" "--port" "$port"
nssm set MutarjimTranslator AppDirectory $projectDir
nssm set MutarjimTranslator AppEnvironmentExtra "AUTO_APPROVE_LOW_CONFIDENCE=1"
nssm set MutarjimTranslator Start SERVICE_AUTO_START
nssm set MutarjimTranslator AppStdout (Join-Path $projectDir "data\service_translator.log")
nssm set MutarjimTranslator AppStderr (Join-Path $projectDir "data\service_translator.log")
Write-Host "Installed service: MutarjimTranslator" -ForegroundColor Green

# --- n8n service -----------------------------------------------------
$n8n = (Get-Command n8n -ErrorAction SilentlyContinue).Source
if ($n8n) {
    nssm install MutarjimN8n $n8n "start"
    nssm set MutarjimN8n Start SERVICE_AUTO_START
    nssm set MutarjimN8n AppStdout (Join-Path $projectDir "data\service_n8n.log")
    nssm set MutarjimN8n AppStderr (Join-Path $projectDir "data\service_n8n.log")
    Write-Host "Installed service: MutarjimN8n" -ForegroundColor Green
} else {
    Write-Host "n8n not found on PATH — skipped. Install n8n service manually if needed." -ForegroundColor Yellow
}

Start-Service MutarjimTranslator
if ($n8n) { Start-Service MutarjimN8n }
Write-Host "`nServices started and set to auto-start on boot." -ForegroundColor Cyan
