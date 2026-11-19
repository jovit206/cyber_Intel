[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param()

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$description = 'Remove only Chapter 11 containers and its dedicated network; retain every named volume and all data.'
if ($PSCmdlet.ShouldProcess('chapter11-sharding Compose project', $description)) {
    docker compose --project-name chapter11-sharding --file (Join-Path $root 'compose.yaml') down
    if ($LASTEXITCODE -ne 0) { throw 'Chapter 11 cleanup failed. No volume-removal option was used.' }
    Write-Host 'Removed only Chapter 11 containers/network. The five chapter11-* data volumes were retained.'
}
