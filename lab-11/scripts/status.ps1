[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$compose = Join-Path $root 'compose.yaml'
docker compose --project-name chapter11-sharding --file $compose ps
if ($LASTEXITCODE -ne 0) { throw 'Could not inspect the Chapter 11 Compose project.' }

$router = & docker exec c11-mongos mongosh --quiet --port 27017 --eval 'printjson({hello: db.hello().msg, shards: db.adminCommand({listShards:1}).shards.map(s => ({name:s._id,host:s.host}))})' 2>$null
if ($LASTEXITCODE -ne 0) { Write-Warning 'mongos is not ready; start the isolated Chapter 11 cluster first.'; exit 1 }
$router
