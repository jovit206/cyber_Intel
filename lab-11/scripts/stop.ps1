[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
docker compose --project-name chapter11-sharding --file (Join-Path $root 'compose.yaml') stop
if ($LASTEXITCODE -ne 0) { throw 'Could not stop the Chapter 11 Compose services.' }
Write-Host 'Stopped only the chapter11-sharding Compose services. Containers and named volumes remain.'
