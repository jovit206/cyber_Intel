[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$projectRoot = Split-Path -Parent (Split-Path -Parent $root)
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw "Project virtual-environment Python not found: $python" }

& (Join-Path $PSScriptRoot 'start.ps1')
if ($LASTEXITCODE -ne 0) { throw 'Chapter 11 cluster startup failed.' }
$env:CHAPTER11_MONGO_URI = 'mongodb://localhost:37317'
& $python (Join-Path $root 'lab11_1_shard_key.py')
if ($LASTEXITCODE -ne 0) { throw 'Lab 11.1 report generation failed.' }
& $python (Join-Path $root 'lab11_2_hotspot.py')
if ($LASTEXITCODE -ne 0) { throw 'Lab 11.2 analysis failed. No existing project data was targeted.' }
& $python (Join-Path $root 'test_lab11.py')
if ($LASTEXITCODE -ne 0) { throw 'Chapter 11 automated checks failed.' }
