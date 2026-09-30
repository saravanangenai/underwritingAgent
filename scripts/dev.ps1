<#
.SYNOPSIS
    Start the underwriting API and the Vue workbench for local development.

.DESCRIPTION
    Opens two PowerShell windows:
      - API        http://localhost:8000   (Swagger UI at /docs; reloads on code changes)
      - Workbench  http://localhost:5173   (hot reload)
    Waits until both answer, then opens the workbench in your browser.
    Close a window, or press Ctrl+C in it, to stop that server.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 -NoBrowser
#>
param(
    [switch]$NoBrowser  # start the servers without opening the browser
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$frontend = Join-Path $root 'frontend'

function Stop-WithMessage([string]$message) {
    Write-Host $message -ForegroundColor Red
    exit 1
}

# --- prerequisites -------------------------------------------------------------------------
foreach ($tool in 'uv', 'npm') {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        Stop-WithMessage "'$tool' was not found on PATH. Install it and open a new terminal."
    }
}
if (-not (Test-Path (Join-Path $root '.env'))) {
    Write-Host "No .env found. Copy .env.example to .env for LLM and Apify keys;" -ForegroundColor Yellow
    Write-Host "structured JSON submissions still work without it." -ForegroundColor Yellow
}
foreach ($port in 8000, 5173) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        Stop-WithMessage "Port $port is already in use. Stop whatever is running there first (is the app already open?)."
    }
}
if (-not (Test-Path (Join-Path $frontend 'node_modules'))) {
    Write-Host 'Installing workbench dependencies (first run only)...'
    Push-Location $frontend
    try { npm install --no-fund --no-audit } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { Stop-WithMessage 'npm install failed.' }
}

# --- start both servers, each in its own window ----------------------------------------------
$api = "`$host.UI.RawUI.WindowTitle = 'Underwriting API :8000'; " +
       "Set-Location '$root'; uv run uvicorn underwriting_agent.api.main:app --reload --port 8000"
$web = "`$host.UI.RawUI.WindowTitle = 'Underwriting Workbench :5173'; " +
       "Set-Location '$frontend'; npm run dev"
Start-Process powershell -ArgumentList '-NoProfile', '-NoExit', '-Command', $api
Start-Process powershell -ArgumentList '-NoProfile', '-NoExit', '-Command', $web

# --- wait until both answer --------------------------------------------------------------------
function Wait-Http([string]$name, [string]$url, [int]$seconds = 90) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 3 | Out-Null
            Write-Host "  $name is up: $url" -ForegroundColor Green
            return $true
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    Write-Host "  $name did not start within $seconds s; check its window for errors." -ForegroundColor Red
    return $false
}

Write-Host 'Waiting for the servers...'
$apiUp = Wait-Http 'API' 'http://localhost:8000/health'
$webUp = Wait-Http 'Workbench' 'http://localhost:5173'

if ($apiUp -and $webUp) {
    Write-Host ''
    Write-Host 'Ready.' -ForegroundColor Green
    Write-Host '  Workbench:   http://localhost:5173'
    Write-Host '  API docs:    http://localhost:8000/docs'
    Write-Host 'Close the two server windows to stop.'
    if (-not $NoBrowser) { Start-Process 'http://localhost:5173' }
} else {
    exit 1
}
