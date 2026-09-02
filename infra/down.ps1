# infra/down.ps1 - stop all shared components (everything in this catalog).

$ErrorActionPreference = "Stop"

$compose = Join-Path $PSScriptRoot "compose.yaml"
Write-Host "Stopping shared infrastructure..." -ForegroundColor Cyan
& docker compose -f $compose down