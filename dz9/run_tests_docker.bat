@echo off
:: Script: run_tests_docker.bat
:: Description: Run model-quality-lab quality gate (ruff, mypy, pytest) in Docker.
:: Works from any checkout location; the repository root is derived from this file.

setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"
set "IMAGE=ghcr.io/astral-sh/uv:python3.13-bookworm-slim"
set "OUTPUT_FILE=%SCRIPT_DIR%test_output.log"

echo.
echo ==========================================
echo Running model-quality-lab quality gate in Docker
echo ==========================================
echo Repository:  %REPO_ROOT%
echo Image:       %IMAGE%
echo Output file: %OUTPUT_FILE%
echo.

docker run --rm -v "%REPO_ROOT%:/app" -w /app "%IMAGE%" sh -c "uv pip install --system -r dz9/requirements.txt >/dev/null && ruff check dz9/src/ --config dz9/pyproject.toml --ignore EXE002 && mypy dz9/src/ --config-file dz9/pyproject.toml --ignore-missing-imports && python -m pytest dz9/tests/ -v -p no:cacheprovider" > "%OUTPUT_FILE%" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"

type "%OUTPUT_FILE%"

echo.
echo ==========================================
if "%EXIT_CODE%"=="0" (
    echo SUCCESS: Quality gate passed.
) else (
    echo FAILED: Quality gate exited with code %EXIT_CODE%.
)
echo ==========================================
exit /b %EXIT_CODE%
