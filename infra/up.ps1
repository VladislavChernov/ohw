# infra/up.ps1 - start shared components declared by a project's infra.yaml.
#
#   .\up.ps1 -Project D:\Otus\Dz2
#   .\up.ps1 -Project D:\Otus\Dz3\simple
#   .\up.ps1 -Project D:\Otus\light_llm_engine
#   .\up.ps1 -Project D:\Otus\Dz2 -Gpu        # NVIDIA CUDA for GPU-capable components
#   .\up.ps1 -Project D:\Otus\Dz2 -Amd        # AMD ROCm image (ollama/ollama:rocm)
#
# Reads <Project>/infra.yaml, takes the `components:` list, and runs
#   docker compose -f compose.yaml --profile <component> ... up -d
# so only the components the project needs are started.

param(
    [Parameter(Mandatory = $true)][string]$Project,
    [switch]$Gpu,
    [switch]$Amd
)

$ErrorActionPreference = "Stop"

$manifest = Join-Path $Project "infra.yaml"
if (-not (Test-Path $manifest)) {
    Write-Host "No infra.yaml found in $Project" -ForegroundColor Red
    exit 1
}

$components = @()
foreach ($line in Get-Content $manifest) {
    if ($line -match '^\s*-\s*(\S+)\s*$') {
        $components += $Matches[1]
    }
}

if ($components.Count -eq 0) {
    Write-Host "No components declared in $manifest - nothing to start." -ForegroundColor Yellow
    exit 0
}

$compose = Join-Path $PSScriptRoot "compose.yaml"
$cmd = @("compose", "-f", $compose)
if ($Gpu) {
    $cmd += @("-f", (Join-Path $PSScriptRoot "compose.gpu.yaml"))
}
if ($Amd) {
    $cmd += @("-f", (Join-Path $PSScriptRoot "compose.amd.yaml"))
}
foreach ($c in $components) {
    $cmd += @("--profile", $c)
}
$cmd += @("up", "-d")

$mode = ""
if ($Gpu) { $mode += " GPU" }
if ($Amd) { $mode += " AMD" }
Write-Host ("Starting shared components ({0}): {1}" -f $mode.Trim(), ($components -join ", "))
& docker @cmd