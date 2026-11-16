#Requires -Version 5.1
<#
.SYNOPSIS
  Runs the graduation project against the shared infra containers.
.DESCRIPTION
  Uses ohw-ollama and the ohw-python:3.13 base image from ..\infra.
  Generated files appear in .\output.

  .\run.ps1              CPU ollama
  .\run.ps1 -Gpu         NVIDIA CUDA ollama (requires NVIDIA runtime)
  .\run.ps1 --count 5    arguments are passed to the compose app service
#>
[CmdletBinding()]
param(
    [switch]$Gpu,
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

$infraMode = if ($Gpu) { 'NVIDIA GPU' } else { 'CPU' }
Write-Host "Infra mode: $infraMode"

if ($Gpu) {
    $runtimes = & docker info --format '{{json .Runtimes}}' 2>$null
    if ($LASTEXITCODE -ne 0 -or "$runtimes" -notmatch '"nvidia"') {
        Write-Error "Docker has no NVIDIA runtime. Install NVIDIA Container Toolkit and enable GPU support in Docker Desktop, or run without -Gpu."
        exit 1
    }
}

$needStart = -not $ollamaRunning
if ($Gpu) { $needStart = $true }
if ($needStart) {
    $infraArgs = @('compose', '-f', '..\infra\compose.yaml')
    if ($Gpu) { $infraArgs += @('-f', '..\infra\compose.gpu.yaml') }
    $infraArgs += @('--profile', 'ollama', 'up', '-d')
    if ($Gpu -and $ollamaRunning) { $infraArgs += '--force-recreate' }
    Write-Host ("Starting shared infra: docker " + ($infraArgs -join ' '))
    & docker @infraArgs
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
