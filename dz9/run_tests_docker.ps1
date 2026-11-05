#!/usr/bin/env powershell
# Script: run_tests_docker.ps1
# Description: Run the model-quality-lab quality gate (ruff, mypy, pytest) in Docker.
# Works from any checkout location; the repository root is derived from this file.
# Usage: powershell.exe -File run_tests_docker.ps1 [-OutputFile path]

param(
    [string]$OutputFile = "$PSScriptRoot\test_output.log"
)

$ParentDir = (Get-Item "$PSScriptRoot\..").FullName.Replace('\', '/')
$DockerVolume = "${ParentDir}:/app"
$Image = "ghcr.io/astral-sh/uv:python3.13-bookworm-slim"

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "Running model-quality-lab quality gate in Docker" -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "Repository:  $ParentDir" -ForegroundColor Cyan
Write-Host "Image:       $Image" -ForegroundColor Cyan
Write-Host "Output file: $OutputFile" -ForegroundColor Cyan
Write-Host ""

docker run --rm -v $DockerVolume -w /app $Image sh -c "uv pip install --system -r dz9/requirements.txt >/dev/null && echo '--- RUNNING RUFF LINTER ---' && ruff check dz9/src dz9/tests --config dz9/pyproject.toml && echo '--- RUNNING MYPY TYPE CHECK ---' && mypy dz9/src/ --config-file dz9/pyproject.toml --ignore-missing-imports && echo '--- RUNNING PYTEST ---' && python -m pytest dz9/tests/ -v -p no:cacheprovider" 2>&1 | Tee-Object -FilePath $OutputFile
$ExitCode = $LASTEXITCODE

Write-Host ""
Write-Host "=========================================" -ForegroundColor Cyan
if ($ExitCode -eq 0) {
    Write-Host "SUCCESS: Quality gate passed." -ForegroundColor Green
} else {
    Write-Host "FAILED: Quality gate exited with code $ExitCode." -ForegroundColor Red
}
Write-Host "=========================================" -ForegroundColor Cyan
exit $ExitCode
