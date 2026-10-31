# GPU stand for dz2 (Windows): shared ollama with the NVIDIA overlay + UI.
# Requires an NVIDIA GPU, driver and NVIDIA Container Toolkit.
# Usage:  .\scripts\up-gpu.ps1
#         .\scripts\up-gpu.ps1 -Rebuild   # force project image rebuild
param(
    [switch]$Rebuild
)

& (Join-Path $PSScriptRoot "_stand.ps1") -Mode gpu -Rebuild:$Rebuild
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
