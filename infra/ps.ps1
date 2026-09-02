# infra/ps.ps1 - status of all shared components in this catalog.

$ErrorActionPreference = "Stop"

$compose = Join-Path $PSScriptRoot "compose.yaml"
& docker compose -f $compose ps -a