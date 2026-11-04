@echo off
:: Script: run_windows.bat
:: Description: Run the model-quality-lab pipeline on Windows via Docker.
:: Usage:
::   run_windows.bat
::   run_windows.bat --dataset breast_cancer --model logistic_regression --train --predict --metrics --visualize
:: Any CLI arguments are passed to model_quality_lab; with no arguments the
:: full demo run (all datasets x all models) is executed.

setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"
set "IMAGE=ghcr.io/astral-sh/uv:python3.13-bookworm-slim"
set "DEFAULT_ARGS=--all-datasets --all-models --train --predict --metrics --visualize"

if "%~1"=="" (
    set "PIPE_ARGS=%DEFAULT_ARGS%"
) else (
    set "PIPE_ARGS=%*"
)

echo.
echo ==========================================
echo model-quality-lab pipeline
echo ==========================================
echo Repository: %REPO_ROOT%
echo Image:      %IMAGE%
echo Arguments:  %PIPE_ARGS%
echo.

docker run --rm -v "%REPO_ROOT%:/app" -w /app/dz9 "%IMAGE%" sh -c "uv pip install --system -r /app/dz9/requirements.txt >/dev/null && PYTHONPATH=/app/dz9/src python -m model_quality_lab %PIPE_ARGS%"

if errorlevel 1 (
    echo.
    echo FAILED: pipeline exited with an error.
    exit /b 1
)

echo.
echo DONE. Plots are in dz9\output\, summary is printed above.
exit /b 0
