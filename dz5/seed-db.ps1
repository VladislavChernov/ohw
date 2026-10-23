# Seed or restore the dz5 SQLite volume.
#
#   .\seed-db.ps1              # initial schema + synthetic seed data
#   .\seed-db.ps1 -Restore     # restore after destructive LLM03 tests
param(
    [switch]$Restore,
    [switch]$Help
)

$ErrorActionPreference = "Stop"

if ($Help) {
    Write-Host "Usage: .\seed-db.ps1 [-Restore]"
    Write-Host ""
    Write-Host "  (default)   Create the schema and load synthetic users/PII"
    Write-Host "  -Restore    Restore the DB to the same seed state"
    exit 0
}

$Root = $PSScriptRoot
$ComposeFile = Join-Path $Root "infra/compose.yaml"

if ($null -eq (Get-Command docker -ErrorAction SilentlyContinue)) {
    [Console]::Error.WriteLine("error: docker not found in PATH")
    exit 127
}
& docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    [Console]::Error.WriteLine("error: docker compose is not available")
    exit 127
}

$service = if ($Restore) { "db-restore" } else { "db-seed" }
& docker compose -f $ComposeFile --profile tools run --rm $service
exit $LASTEXITCODE
