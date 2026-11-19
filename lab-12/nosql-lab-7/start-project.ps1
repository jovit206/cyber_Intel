[CmdletBinding()]
param(
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$mongoContainers = @('fresh-mongo1', 'fresh-mongo2', 'fresh-mongo3')
$mongoPorts = @{ 'fresh-mongo1' = '37017'; 'fresh-mongo2' = '37018'; 'fresh-mongo3' = '37019' }
$backendContainer = 'fresh-backend'
$requiredNetwork = 'fresh-mongo-net'
$frontendUrl = 'http://localhost:8000'

function Get-ContainerState([string]$name) {
    $state = & docker inspect --format '{{.State.Status}}' $name 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "Required existing container '$name' was not found. This script never creates or recreates containers."
    }
    return [string]($state | Select-Object -First 1)
}

function Assert-ExistingContainer([string]$name, [string]$expectedPort = '') {
    $networksJson = & docker inspect --format '{{json .NetworkSettings.Networks}}' $name
    if ($LASTEXITCODE -ne 0) { throw "Could not inspect existing container '$name'." }
    $networks = $networksJson | ConvertFrom-Json
    if ($networks.PSObject.Properties.Name -notcontains $requiredNetwork) {
        throw "Container '$name' is not attached to '$requiredNetwork'; refusing to alter its network."
    }
    if ($expectedPort) {
        $bindingsJson = & docker inspect --format '{{json .HostConfig.PortBindings}}' $name
        if ($LASTEXITCODE -ne 0) { throw "Could not inspect port bindings for '$name'." }
        $bindings = $bindingsJson | ConvertFrom-Json
        $binding = $bindings.'27017/tcp' | Select-Object -First 1
        if (!$binding -or [string]$binding.HostPort -ne $expectedPort) {
            throw "Container '$name' is not using its verified loopback port $expectedPort; refusing to start it."
        }
    }
}

function Start-ExistingContainer([string]$name) {
    $state = Get-ContainerState $name
    if ($state -ne 'running') {
        Write-Host "Starting existing container $name (state: $state)..."
        $null = & docker start $name
        if ($LASTEXITCODE -ne 0) { throw "Docker could not start '$name'. Check Docker Desktop and port conflicts." }
    } else {
        Write-Host "Container $name is already running."
    }
}

function Wait-ForHealthyContainer([string]$name, [int]$timeoutSeconds = 150) {
    $deadline = (Get-Date).AddSeconds($timeoutSeconds)
    do {
        $health = & docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' $name 2>$null
        if ($LASTEXITCODE -ne 0) { throw "Could not check health for '$name'." }
        $state = Get-ContainerState $name
        if ($state -ne 'running') { throw "Container '$name' stopped while starting (state: $state)." }
        if ([string]$health -eq 'healthy' -or [string]$health -eq 'none') { return }
        if ([string]$health -eq 'unhealthy') { throw "Container '$name' is unhealthy. Inspect it with: docker logs --tail 100 $name" }
        Start-Sleep -Seconds 3
    } while ((Get-Date) -lt $deadline)
    throw "Timed out waiting for '$name' to become healthy. Inspect it with: docker logs --tail 100 $name"
}

try {
    Set-Location -LiteralPath $projectRoot
    if (!(Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker CLI was not found. Start Docker Desktop and install/enable its Docker CLI.' }
    $serverVersion = & docker info --format '{{.ServerVersion}}' 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'Docker Desktop is not ready. Start it, wait for the engine, then run this script again.' }
    Write-Host "Docker Engine $serverVersion is available."

    foreach ($name in $mongoContainers) { Assert-ExistingContainer $name $mongoPorts[$name] }
    Assert-ExistingContainer $backendContainer

    # Start only the existing Lab 7 replica-set containers, one at a time.
    foreach ($name in $mongoContainers) { Start-ExistingContainer $name }
    foreach ($name in $mongoContainers) { Wait-ForHealthyContainer $name }

    $replicaProbe = "const s = rs.status(); print(JSON.stringify({set:s.set,members:s.members.map(m=>({name:m.name,state:m.stateStr,health:m.health}))}));"
    $replicaStatus = $null
    $replicaDeadline = (Get-Date).AddSeconds(180)
    do {
        $probeOutput = & docker exec fresh-mongo1 mongosh --quiet --eval $replicaProbe 2>$null
        if ($LASTEXITCODE -eq 0) {
            $jsonLine = $probeOutput | Where-Object { ([string]$_).Trim().StartsWith('{') } | Select-Object -Last 1
            if ($jsonLine) {
                try { $replicaStatus = $jsonLine | ConvertFrom-Json } catch { $replicaStatus = $null }
            }
        }
        $members = @($replicaStatus.members)
        $primaries = @($members | Where-Object { $_.state -eq 'PRIMARY' -and $_.health -eq 1 }).Count
        $secondaries = @($members | Where-Object { $_.state -eq 'SECONDARY' -and $_.health -eq 1 }).Count
        $healthyCount = @($members | Where-Object { $_.health -eq 1 }).Count
        if ($replicaStatus.set -eq 'rs0' -and $primaries -eq 1 -and $secondaries -eq 2 -and $healthyCount -eq 3) { break }
        Start-Sleep -Seconds 3
    } while ((Get-Date) -lt $replicaDeadline)
    if (!$replicaStatus -or $replicaStatus.set -ne 'rs0' -or $primaries -ne 1 -or $secondaries -ne 2 -or $healthyCount -ne 3) {
        throw 'Replica set rs0 did not reach one healthy PRIMARY and two healthy SECONDARY members. No reconfiguration was attempted.'
    }
    Write-Host 'Replica set rs0 is healthy: 1 PRIMARY, 2 SECONDARY members.'

    Start-ExistingContainer $backendContainer
    Wait-ForHealthyContainer $backendContainer 150

    $healthResponse = Invoke-RestMethod -Uri "$frontendUrl/health" -TimeoutSec 15
    if ($healthResponse.status -ne 'ok') { throw 'The backend health endpoint did not return status=ok.' }
    $page = Invoke-WebRequest -UseBasicParsing -Uri "$frontendUrl/account" -TimeoutSec 15
    if ($page.StatusCode -ne 200 -or $page.Content -notmatch 'Welcome back') { throw 'The sign-in page did not load correctly.' }
    try {
        Invoke-WebRequest -UseBasicParsing -Uri "$frontendUrl/api/meta" -TimeoutSec 15 -ErrorAction Stop | Out-Null
        throw 'The protected API unexpectedly accepted an unauthenticated request.'
    } catch {
        if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 401) { }
        elseif ($_.Exception.Message -like '*unexpectedly accepted*') { throw }
        else { throw "Could not verify API authentication: $($_.Exception.Message)" }
    }

    Write-Host ''
    Write-Host 'Project is ready:' -ForegroundColor Green
    Write-Host "  Frontend:       $frontendUrl"
    Write-Host "  Backend health: $frontendUrl/health"
    Write-Host "  API docs:       $frontendUrl/docs"
    Write-Host '  Protected APIs: anonymous requests return HTTP 401'
    if (!$NoBrowser) {
        try { Start-Process $frontendUrl } catch { Write-Warning "Open $frontendUrl in your browser." }
    }
    Write-Host 'Services run in Docker in the background; this PowerShell window can be closed.'
} catch {
    Write-Host "Startup failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'No containers were created, removed, reconfigured, or reset by this script.' -ForegroundColor Yellow
    exit 1
}
