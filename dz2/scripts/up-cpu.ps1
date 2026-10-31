# CPU stand for dz2 (Windows): shared ollama on CPU + workbench UI.
# Usage:  .\scripts\up-cpu.ps1
#         .\scripts\up-cpu.ps1 -Rebuild   # force project image rebuild
param(
    [switch]$Rebuild
)

& (Join-Path $PSScriptRoot "_stand.ps1") -Mode cpu -Rebuild:$Rebuild
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
