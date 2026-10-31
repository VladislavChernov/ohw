# Stops the dz2 stand (Windows).
# Usage:  .\scripts\down.ps1           # project ui only
#         .\scripts\down.ps1 -Infra    # also stop shared ollama (other projects too!)
param(
    [switch]$Infra
)

$ErrorActionPreference = "Stop"

$projectDir = Split-Path $PSScriptRoot -Parent
$repoDir = Split-Path $projectDir -Parent
$composeFile = Join-Path $projectDir "compose.yaml"

& docker compose -f $composeFile down
if ($LASTEXITCODE -ne 0) {
    throw "Failed to stop the dz2 ui service"
}

if ($Infra) {
    $infraCompose = Join-Path (Join-Path $repoDir "infra") "compose.yaml"
    & docker compose -f $infraCompose down
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to stop shared infrastructure"
    }
}

Write-Host "dz2 stand stopped."
