# Runs every Lab 2 activity, then rebuilds REPORT.md from the saved outputs.
# Usage (from this folder):  powershell -NoProfile -ExecutionPolicy Bypass -File .\run_all.ps1 [-Python <path>]
param([string]$Python = "")
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not $Python) {
    $venv = Join-Path $PSScriptRoot "..\..\.venv\Scripts\python.exe"
    $Python = if (Test-Path $venv) { $venv } else { "python" }
}
$running = docker inspect jovit-lab02-mongo --format "{{.State.Running}}" 2>$null
if ($running -ne "true") {
    Write-Host "Starting the jovit-lab02-mongo container..." -ForegroundColor Yellow
    docker start jovit-lab02-mongo | Out-Null
    Start-Sleep -Seconds 5
}
$scripts = "activity_2_1_environment_check.py", "activity_2_1_try_it_yourself.py",
           "activity_2_2_bookstore_crud.py", "activity_2_2_try_it_yourself.py"
foreach ($script in $scripts) {
    Write-Host "`n>>> $script" -ForegroundColor Cyan
    & $Python $script
    if ($LASTEXITCODE -ne 0) { throw "$script failed with exit code $LASTEXITCODE" }
}
& $Python build_report.py
if ($LASTEXITCODE -ne 0) { throw "build_report.py failed" }
