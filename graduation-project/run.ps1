#Requires -Version 5.1
<#
.SYNOPSIS
  Runs the graduation project against the shared infra containers.
.DESCRIPTION
  Uses ohw-ollama and the ohw-python:3.13 base image from ..\infra.
  Generated files appear in .\output.
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $Arguments
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path "..\infra\compose.yaml")) {
    Write-Error "..\infra not found; this project uses the shared ohw infra."
    exit 1
}

$networkExists = $true
docker network inspect ohw_net *> $null
if ($LASTEXITCODE -ne 0) { $networkExists = $false }

$ollamaRunning = $false
if ($networkExists) {
    $state = docker inspect --format "{{.State.Running}}" ohw-ollama 2>$null
    $ollamaRunning = ($state -eq "true")
}

if (-not $ollamaRunning) {
    Write-Host "Starting shared infra (profile ollama)..."
    docker compose -f ..\infra\compose.yaml --profile ollama up -d
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

$healthy = $false
for ($i = 0; $i -lt 90; $i++) {
    $health = docker inspect --format "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}" ohw-ollama 2>$null
    if ($health -eq "healthy" -or $health -eq "none") { $healthy = $true; break }
    Start-Sleep -Seconds 2
}
if (-not $healthy) {
    Write-Warning "ohw-ollama did not become healthy in 180s; continuing."
}

$command = @("compose")
if ($Arguments.Count -gt 0) {
    $command += @("run", "--rm", "--build", "app") + $Arguments
} else {
    $command += @(
        "up", "--build", "--abort-on-container-exit",
        "--exit-code-from", "app"
    )
}

Write-Host ("Running: docker " + ($command -join " "))
& docker @command
exit $LASTEXITCODE
