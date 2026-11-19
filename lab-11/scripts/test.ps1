[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$projectRoot = Split-Path -Parent (Split-Path -Parent $root)
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw "Project virtual-environment Python not found: $python" }
$env:CHAPTER11_MONGO_URI = 'mongodb://localhost:37317'
& $python (Join-Path $root 'test_lab11.py')
if ($LASTEXITCODE -ne 0) { throw 'One or more Chapter 11 automated checks failed.' }
