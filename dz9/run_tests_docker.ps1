#!/usr/bin/env powershell
# Script: run_tests_docker.ps1
# Description: Full Quality Pipeline in one-line string to prevent Windows CRLF issues
# Usage: powershell.exe -File run_tests_docker.ps1

param(
    [string]$OutputFile = "test_output.log"
)

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "Starting Quality Pipeline with uv" -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "Output file: $OutputFile" -ForegroundColor Cyan
Write-Host ""

# Поднимаемся на уровень выше текущей папки dz9
$ParentDir = (Get-Item "$PSScriptRoot\..").FullName.Replace('\', '/')
$DockerVolume = "${ParentDir}:/app"

# Все команды выстроены в одну строку через кодовые связки &&. Никаких скрытых CRLF.
docker run --rm -v $DockerVolume -w /app ghcr.io/astral-sh/uv:python3.13-bookworm-slim sh -c "uv pip install --system -r dz9/requirements.txt && echo '--- CLEANING UNUSED CODE ---' && ruff check dz9/src/ --fix --unsafe-fixes --ignore EXE002 && echo '--- RUNNING RUFF LINTER ---' && ruff check dz9/src/ --ignore EXE002 && echo '--- RUNNING MYPY TYPE CHECK ---' && mypy dz9/src/ --ignore-missing-imports && echo '--- RUNNING PYTEST ---' && python -m pytest dz9/tests/test_basic.py -v -p no:cacheprovider" 2>&1 | Out-File -FilePath $OutputFile -Encoding UTF8

Write-Host ""
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "Pipeline completed. Output saved to: $OutputFile" -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan
