<#
.SYNOPSIS  Stop Jenkins and Minikube (data is kept: Jenkins volume, Minikube disk). -Purge removes them.
#>
[CmdletBinding()]
param([switch]$Purge)
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$compose = Join-Path $Root 'ci\jenkins\docker-compose.yml'
if ($Purge) { docker compose -f $compose down --volumes } else { docker compose -f $compose down }
$mk = Get-Command minikube -ErrorAction SilentlyContinue
if (-not $mk) { $mk = Join-Path $env:LOCALAPPDATA 'Programs\minikube\minikube.exe' } else { $mk = $mk.Source }
if (Test-Path $mk) { if ($Purge) { & $mk delete } else { & $mk stop } }
