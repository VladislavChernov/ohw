# dz6/run.ps1 — start the shared infra (ollama) and run the test optimizer.
#
#   .\run.ps1                  # CPU ollama, input from .\input
#   .\run.ps1 -Gpu             # NVIDIA CUDA ollama
#   .\run.ps1 -Rebuild         # rebuild the app image without cache
#   .\run.ps1 -InputFolder .\other\dir
#
# Requires Docker Desktop with the compose plugin and the shared infra catalog
# at ..\infra (see ..\infra\README.md).

[CmdletBinding()]
param(
    [switch]$Gpu,
    [switch]$Rebuild,
    [string]$InputFolder
)

$ErrorActionPreference = 'Stop'

$scriptDir = $PSScriptRoot
$infraDir = (Resolve-Path (Join-Path $scriptDir '..\infra')).Path
$infraCompose = Join-Path $infraDir 'compose.yaml'
if (-not (Test-Path $infraCompose)) {
    throw "Not found: $infraCompose. Run from D:\Otus\ohw\dz6."
}

$compose = @('compose', '-f', (Join-Path $scriptDir 'compose.yaml'), '--project-directory', $scriptDir)

function Invoke-Docker {
    param([string[]]$Arguments)
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker $($Arguments -join ' ') failed with exit code $LASTEXITCODE"
    }
}

Write-Host '==> [1/3] starting shared infra (ollama)' -ForegroundColor Cyan
$infraArgs = @('compose', '-f', $infraCompose)
if ($Gpu) { $infraArgs += @('-f', (Join-Path $infraDir 'compose.gpu.yaml')) }
$infraArgs += @('--profile', 'ollama', 'up', '-d')
Invoke-Docker $infraArgs

Write-Host '==> waiting for ohw-ollama to become ready' -ForegroundColor Cyan
$status = 'missing'
for ($i = 0; $i -lt 180; $i++) {
    $raw = & docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' ohw-ollama 2>$null
    if ($LASTEXITCODE -eq 0 -and $raw) { $status = @($raw)[0] }
    if ($status -eq 'healthy' -or $status -eq 'running') { break }
    Start-Sleep -Seconds 2
}
if ($status -ne 'healthy' -and $status -ne 'running') {
    & docker logs --tail 50 ohw-ollama
    throw "ohw-ollama is not ready (status: $status)."
}
Write-Host "    ohw-ollama: $status" -ForegroundColor Gray

Write-Host '==> [2/3] building app image' -ForegroundColor Cyan
$buildArgs = $compose + @('build')
if ($Rebuild) { $buildArgs += '--no-cache' }
$buildArgs += 'app'
Invoke-Docker $buildArgs

Write-Host '==> [3/3] running the optimizer' -ForegroundColor Cyan
$runArgs = $compose + @('run', '--rm')
if ($InputFolder) {
    $resolved = (Resolve-Path $InputFolder).Path
    $runArgs += @('-e', 'INPUT_DIR=/data/tests')
    $runArgs += @('--mount', "type=bind,source=$resolved,target=/data/tests,readonly")
}
$runArgs += 'app'
Invoke-Docker $runArgs

$outputDir = Join-Path $scriptDir 'output'
Write-Host ''
$runs = @(
    Get-ChildItem -Path $outputDir -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$' } |
        Sort-Object Name -Descending
)
if ($runs.Count -eq 0) {
    throw 'output/ has no run directories — the run produced no artifacts.'
}
$latest = $runs[0]
Write-Host "Done. Latest run: $($latest.Name)" -ForegroundColor Green
Write-Host "Results in: $($latest.FullName)"
Get-ChildItem -Path $latest.FullName -File | Select-Object Name, Length | Format-Table -AutoSize
