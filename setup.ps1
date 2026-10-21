# Orbital Intelligence System - Windows setup
# Run from the project directory in PowerShell: .\setup.ps1

$ErrorActionPreference = 'Stop'
$ProjectDir = $PSScriptRoot
Set-Location $ProjectDir

function Write-Step([string]$Message) { Write-Host "`n==> $Message" -ForegroundColor Cyan }
function Write-Info([string]$Message) { Write-Host "  [i] $Message" -ForegroundColor DarkCyan }
function Write-Warn([string]$Message) { Write-Host "  [!] $Message" -ForegroundColor Yellow }

try {
    Write-Step 'Checking prerequisites'

    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $PythonCommand) {
        throw 'Python 3.14 or newer was not found. Install it from python.org and enable the Python PATH option.'
    }
    $PythonVersion = & $PythonCommand.Source -c 'import sys; print("{}.{}".format(*sys.version_info[:2]))'
    $VersionParts = $PythonVersion.Trim().Split('.') | ForEach-Object { [int]$_ }
    if ($VersionParts[0] -lt 3 -or ($VersionParts[0] -eq 3 -and $VersionParts[1] -lt 14)) {
        throw "Python 3.14 or newer is required; found Python $PythonVersion."
    }
    Write-Host "  Found Python $PythonVersion at $($PythonCommand.Source)" -ForegroundColor Green

    $DockerCommand = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $DockerCommand) {
        throw 'Docker was not found. Install Docker Desktop for Windows, start it, then rerun this script.'
    }
    & docker compose version | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Docker Compose is unavailable. Update Docker Desktop, start it, then rerun this script.'
    }
    & docker info | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Docker Desktop is installed but does not appear to be running. Start it, then rerun this script.'
    }

    Write-Step 'Configuring environment'
    if (-not (Test-Path (Join-Path $ProjectDir '.env'))) {
        Copy-Item (Join-Path $ProjectDir '.env.example') (Join-Path $ProjectDir '.env')
        Write-Host '  Created .env from .env.example' -ForegroundColor Green
    } else {
        Write-Info 'Keeping existing .env'
    }

    Write-Step 'Installing Python dependencies'
    $VenvPython = Join-Path $ProjectDir '.venv\Scripts\python.exe'
    if (-not (Test-Path $VenvPython)) {
        & $PythonCommand.Source -m venv (Join-Path $ProjectDir '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Could not create .venv.' }
    }
    & $VenvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw 'Could not upgrade pip in .venv.' }
    & $VenvPython -m pip install -r (Join-Path $ProjectDir 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Could not install Python dependencies.' }
    Write-Host '  Python environment is ready.' -ForegroundColor Green

    Write-Step 'Starting MariaDB with Docker Compose'
    & docker compose up -d
    if ($LASTEXITCODE -ne 0) { throw 'Could not start MariaDB. Check Docker Desktop and try again.' }

    $DbPort = 3307
    $PortLine = Get-Content (Join-Path $ProjectDir '.env') | Where-Object { $_ -match '^OIS_DB_PORT\s*=' } | Select-Object -Last 1
    if ($PortLine -match '^OIS_DB_PORT\s*=\s*(\d+)') { $DbPort = [int]$Matches[1] }
    Write-Info "Waiting for MariaDB on 127.0.0.1:$DbPort"
    $Ready = $false
    for ($Attempt = 0; $Attempt -lt 60; $Attempt++) {
        $Client = [System.Net.Sockets.TcpClient]::new()
        try {
            $Connect = $Client.BeginConnect('127.0.0.1', $DbPort, $null, $null)
            if ($Connect.AsyncWaitHandle.WaitOne(1000) -and $Client.Connected) {
                $Client.EndConnect($Connect)
                $Ready = $true
                break
            }
        } catch { }
        finally { $Client.Dispose() }
        Start-Sleep -Seconds 1
    }

    if ($Ready) {
        Write-Step 'Initializing the database and loading starter data'
        & $VenvPython -m app --init-db
        if ($LASTEXITCODE -ne 0) { throw 'Database initialization failed. Check the database settings in .env.' }
        & $VenvPython -m app --sync
        if ($LASTEXITCODE -ne 0) { Write-Warn 'Initial data sync failed. Retry later with .\ois.cmd --sync.' }
    } else {
        Write-Warn "MariaDB did not become reachable on port $DbPort. Start it with 'docker compose up -d', then run .\ois.cmd --init-db and .\ois.cmd --sync."
    }

    Write-Step 'Setup complete'
    Write-Host '  Launch Mission Control with .\ois.cmd' -ForegroundColor Green
    Write-Host '  Examples: .\ois.cmd --alerts | .\ois.cmd --view 25544 | .\ois.cmd --sync'
} catch {
    Write-Host "`nSetup failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
