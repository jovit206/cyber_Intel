[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$compose = Join-Path $root 'compose.yaml'
$project = 'chapter11-sharding'

$ports = @(
    @{ Number = 37117; Container = 'c11-config1' },
    @{ Number = 37118; Container = 'c11-config2' },
    @{ Number = 37119; Container = 'c11-config3' },
    @{ Number = 37217; Container = 'c11-shard1' },
    @{ Number = 37218; Container = 'c11-shard2' },
    @{ Number = 37317; Container = 'c11-mongos' }
)

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker CLI is required.' }
& docker info --format '{{.ServerVersion}}' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Docker Engine is unavailable. Start Docker Desktop and retry.' }

$running = @(& docker ps --format '{{.Names}}|{{.Ports}}')
foreach ($entry in $ports) {
    $port = [int]$entry.Number
    $owner = $running | Where-Object { $_ -match ('^' + [regex]::Escape($entry.Container) + '\|') -and $_ -match (':' + $port + '->') }
    $otherDockerOwner = $running | Where-Object { $_ -match (':' + $port + '->') -and $_ -notmatch ('^' + [regex]::Escape($entry.Container) + '\|') }
    if ($otherDockerOwner) { throw "Host port $port is already published by another Docker container: $otherDockerOwner" }
    $listener = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    if ($listener -and -not $owner) { throw "Host port $port has a listener that is not the Chapter 11 container. No containers were started." }
}

& docker compose --project-name $project --file $compose up --detach --wait --wait-timeout 180 config1 config2 config3 shard1 shard2
if ($LASTEXITCODE -ne 0) { throw 'Chapter 11 config/shard members did not become healthy. Existing non-Chapter 11 containers were not targeted.' }

& docker exec c11-config1 mongosh --quiet --port 27019 --file /lab11-scripts/init-config-rs.js
if ($LASTEXITCODE -ne 0) { throw 'Could not safely initialize c11-config-rs.' }
& docker exec c11-shard1 mongosh --quiet --port 27018 --file /lab11-scripts/init-shard1-rs.js
if ($LASTEXITCODE -ne 0) { throw 'Could not safely initialize c11-shard1-rs.' }
& docker exec c11-shard2 mongosh --quiet --port 27018 --file /lab11-scripts/init-shard2-rs.js
if ($LASTEXITCODE -ne 0) { throw 'Could not safely initialize c11-shard2-rs.' }

function Wait-ReplicaSet([string]$container, [int]$port, [string]$setName) {
    for ($i = 0; $i -lt 90; $i++) {
        $state = & docker exec $container mongosh --quiet --port $port --eval "try { const s = rs.status(); print(s.members.some(m => m.stateStr === 'PRIMARY') ? 'READY' : 'WAIT'); } catch (e) { print('WAIT'); }" 2>$null
        if ($LASTEXITCODE -eq 0 -and [string]($state | Select-Object -Last 1).Trim() -eq 'READY') { return }
        Start-Sleep -Seconds 2
    }
    throw "Timed out waiting for primary in $setName. Inspect only the Chapter 11 logs with docker logs c11-config1."
}

Wait-ReplicaSet 'c11-config1' 27019 'c11-config-rs'
Wait-ReplicaSet 'c11-shard1' 27018 'c11-shard1-rs'
Wait-ReplicaSet 'c11-shard2' 27018 'c11-shard2-rs'

& docker compose --project-name $project --file $compose up --detach --wait --wait-timeout 120 mongos
if ($LASTEXITCODE -ne 0) { throw 'mongos did not become healthy after replica-set initialization.' }

& docker exec c11-mongos mongosh --quiet --port 27017 --file /lab11-scripts/init-mongos.js
if ($LASTEXITCODE -ne 0) { throw 'Could not register the isolated Chapter 11 shards through mongos.' }

Write-Host 'Chapter 11 sharded cluster is ready.'
Write-Host 'mongos: mongodb://localhost:37317/chapter11'
Write-Host "Use: `$env:CHAPTER11_MONGO_URI='mongodb://localhost:37317'"
