<#
.SYNOPSIS
  Start Minikube and prepare it for the Jenkins CD stage (run once, before Jenkins).
.DESCRIPTION
  1. (with -Install) download minikube v1.39.0 and verify its SHA-256
  2. minikube start --driver=docker (creates the "minikube" Docker network Jenkins joins)
  3. kubectl apply -f k8s/cluster  (namespace "timetable" + ServiceAccount/Role for Jenkins)
  4. write ci/jenkins/secrets/kubeconfig: token of jenkins-deployer, rights in namespace "timetable" only
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts/devops/minikube-setup.ps1 -Install
#>
[CmdletBinding()]
param(
    [switch]$Install,
    [int]$Cpus = 6,
    [string]$Memory = '8g',
    [string]$MinikubeVersion = 'v1.39.0'
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$LocalDir = Join-Path $env:LOCALAPPDATA 'Programs\minikube'

function Find-Minikube {
    $cmd = Get-Command minikube -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $local = Join-Path $LocalDir 'minikube.exe'
    if (Test-Path $local) { return $local }
    return $null
}

$mk = Find-Minikube
if (-not $mk) {
    if (-not $Install) { throw 'minikube not found. Re-run with -Install (or: winget install Kubernetes.minikube).' }
    New-Item -ItemType Directory -Force -Path $LocalDir | Out-Null
    $base = "https://github.com/kubernetes/minikube/releases/download/$MinikubeVersion/minikube-windows-amd64.exe"
    $exe = Join-Path $LocalDir 'minikube.exe'
    Write-Host "Downloading minikube $MinikubeVersion"
    Invoke-WebRequest "$base" -OutFile $exe -UseBasicParsing
    Invoke-WebRequest "$base.sha256" -OutFile "$exe.sha256" -UseBasicParsing
    $expected = ((Get-Content "$exe.sha256" -Raw).Trim() -split '\s+')[0]
    Remove-Item "$exe.sha256"
    $actual = (Get-FileHash $exe -Algorithm SHA256).Hash
    if ($actual -ne $expected.ToUpper()) { Remove-Item $exe; throw "minikube checksum mismatch: $actual" }
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if ($userPath -notlike "*$LocalDir*") { [Environment]::SetEnvironmentVariable('Path', "$userPath;$LocalDir", 'User') }
    $mk = $exe
}
Write-Host "minikube: $(& $mk version --short)"

& $mk status --format '{{.Host}}' 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Starting Minikube (docker driver, $Cpus CPU, $Memory)"
    & $mk start --driver=docker --cpus=$Cpus --memory=$Memory
    if ($LASTEXITCODE -ne 0) { throw 'minikube start failed' }
} else {
    Write-Host 'Minikube is already running'
}

kubectl --context minikube apply -f (Join-Path $Root 'k8s\cluster')  # 00-namespace first, then RBAC
if ($LASTEXITCODE -ne 0) { throw 'kubectl apply k8s/cluster failed' }

$token = ''
for ($i = 0; $i -lt 30 -and -not $token; $i++) {
    $token = kubectl --context minikube -n timetable get secret jenkins-deployer-token -o 'jsonpath={.data.token}'
    if (-not $token) { Start-Sleep -Seconds 1 }
}
if (-not $token) { throw 'ServiceAccount token was not issued' }
$ca = kubectl --context minikube -n timetable get secret jenkins-deployer-token -o 'jsonpath={.data.ca\.crt}'
$plainToken = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($token))
$ip = (& $mk ip).Trim()

$kubeconfig = @"
apiVersion: v1
kind: Config
clusters:
  - name: minikube
    cluster:
      server: https://${ip}:8443
      certificate-authority-data: $ca
users:
  - name: jenkins-deployer
    user:
      token: $plainToken
contexts:
  - name: jenkins-deployer@minikube
    context:
      cluster: minikube
      user: jenkins-deployer
      namespace: timetable
current-context: jenkins-deployer@minikube
"@
$secrets = Join-Path $Root 'ci\jenkins\secrets'
New-Item -ItemType Directory -Force -Path $secrets | Out-Null
$target = Join-Path $secrets 'kubeconfig'
[IO.File]::WriteAllText($target, ($kubeconfig -replace "`r`n", "`n"), (New-Object Text.UTF8Encoding($false)))
Write-Host "Wrote $target (server https://${ip}:8443)"

# Least privilege check: the Jenkins identity may deploy the app but cannot read Secrets.
# (The node IP is reachable only from containers on the "minikube" Docker network, not from Windows,
#  so the check impersonates the ServiceAccount through the admin context.)
$sa = 'system:serviceaccount:timetable:jenkins-deployer'
$canDeploy = kubectl --context minikube auth can-i patch deployments -n timetable --as $sa
$canSecrets = kubectl --context minikube auth can-i get secrets -n timetable --as $sa 2>$null
Write-Host "jenkins-deployer: patch deployments=$canDeploy, get secrets=$canSecrets"
if ($canDeploy -ne 'yes') { throw 'jenkins-deployer cannot deploy: check k8s/cluster/jenkins-deployer.yaml' }
