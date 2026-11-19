# Runs every Lab 6 activity against MongoDB, then rebuilds REPORT.md from the saved outputs.
# Usage (from this folder):  powershell -NoProfile -ExecutionPolicy Bypass -File .\run_all.ps1 [-Python <path>]
param([string]$Python = "")
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not $Python) {
    $venv = Join-Path $PSScriptRoot "..\..\.venv\Scripts\python.exe"
    $Python = if (Test-Path $venv) { $venv } else { "python" }
}
$scripts = "activity_6_1_index_design.py", "activity_6_1_try_it_yourself.py",
           "activity_6_2_index_strategies.py", "activity_6_2_try_it_yourself.py"
foreach ($script in $scripts) {
    Write-Host "`n>>> $script" -ForegroundColor Cyan
    & $Python $script
    if ($LASTEXITCODE -ne 0) { throw "$script failed with exit code $LASTEXITCODE" }
}
& $Python build_report.py
if ($LASTEXITCODE -ne 0) { throw "build_report.py failed" }
