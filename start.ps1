$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$backend = Join-Path $root "backend"
$observer = Join-Path $root "observer"
$attacker = Join-Path $root "attacker"
$venv = Join-Path $root ".venv"
$pythonExe = Join-Path $venv "Scripts\python.exe"

function Stop-Port {
    param([int]$Port)
    $connections = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
    $processIds = $connections | Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($processId in $processIds) {
        if ($processId -and $processId -ne $PID) {
            Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
        }
    }
}

function Ensure-NodeModules {
    param([string]$Path)
    if (-not (Test-Path (Join-Path $Path "node_modules"))) {
        Push-Location $Path
        npm install
        Pop-Location
    }
}

Stop-Port -Port 8000

if (-not (Test-Path $pythonExe)) {
    python -m venv $venv
}

& $pythonExe -m pip install --upgrade pip
& $pythonExe -m pip install -r (Join-Path $backend "requirements.txt")

Ensure-NodeModules -Path $observer
Ensure-NodeModules -Path $attacker

$backendCommand = "cd `"$backend`"; `"$pythonExe`" -m uvicorn main:app --host 127.0.0.1 --port 8000 --ws-ping-interval 20 --ws-ping-timeout 20"
$observerCommand = "cd `"$observer`"; npm run dev"
$attackerCommand = "cd `"$attacker`"; npm run dev"

Start-Process powershell -WindowStyle Hidden -ArgumentList "-NoExit", "-Command", $backendCommand
Start-Process powershell -WindowStyle Hidden -ArgumentList "-NoExit", "-Command", $observerCommand
Start-Process powershell -WindowStyle Hidden -ArgumentList "-NoExit", "-Command", $attackerCommand

Start-Sleep -Seconds 3
Start-Process "http://127.0.0.1:3000"
Start-Process "http://127.0.0.1:3001"

Write-Host "SentinelMesh running:"
Write-Host "  Backend:  http://127.0.0.1:8000"
Write-Host "  Observer: http://127.0.0.1:3000"
Write-Host "  Attacker: http://127.0.0.1:3001"
