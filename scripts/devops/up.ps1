<#
.SYNOPSIS
  Bring up the whole local DevOps stack: Minikube (CD target) + Jenkins (CI/CD server).
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts/devops/up.ps1 -InstallMinikube
#>
[CmdletBinding()]
param([switch]$InstallMinikube, [switch]$SkipMinikube)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$envFile = Join-Path $Root 'ci\jenkins\.env'

if (-not (Test-Path $envFile)) {
    $bytes = New-Object byte[] 18
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $password = [Convert]::ToBase64String($bytes).Replace('+', 'A').Replace('/', 'b').TrimEnd('=')
    $text = (Get-Content (Join-Path $Root 'ci\jenkins\.env.example') -Raw) -replace 'JENKINS_ADMIN_PASSWORD=', "JENKINS_ADMIN_PASSWORD=$password"
    [IO.File]::WriteAllText($envFile, ($text -replace "`r`n", "`n"), (New-Object Text.UTF8Encoding($false)))
    Write-Host "Created ci/jenkins/.env with a random admin password"
}

if (-not $SkipMinikube) {
    & (Join-Path $PSScriptRoot 'minikube-setup.ps1') -Install:$InstallMinikube
}

docker compose -f (Join-Path $Root 'ci\jenkins\docker-compose.yml') up -d --build
if ($LASTEXITCODE -ne 0) { throw 'docker compose up failed' }

Write-Host 'Waiting for Jenkins on http://localhost:8080 ...'
for ($i = 0; $i -lt 90; $i++) {
    try {
        $r = Invoke-WebRequest 'http://localhost:8080/login' -UseBasicParsing -TimeoutSec 3
        if ($r.StatusCode -eq 200) { break }
    } catch { Start-Sleep -Seconds 2 }
}
Write-Host 'Jenkins is up: http://localhost:8080  (login: see ci/jenkins/.env)'
Write-Host 'Job "multi-agent-timetable" scans GitHub every minute and builds every new commit.'
