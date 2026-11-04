@echo off
:: Script: run_all_models.bat
:: Description: Run all models sequentially and generate comparison summary

echo.
echo ==========================================
echo Running all models sequentially
echo ==========================================
echo.
echo Image: ghcr.io/astral-sh/uv:python3.13-bookworm-slim
echo Project: D:\Otus\ohw
echo.

docker run --rm -v "D:/Otus/ohw:/app" -w /app "ghcr.io/astral-sh/uv:python3.13-bookworm-slim" sh -c "uv pip install --system -r dz9/requirements.txt && PYTHONPATH=/app/dz9/src python -m model_quality_lab.__main__ --all-models --train --predict --metrics --visualize" >> run_all_models_output.log 2>&1

echo.
if errorlevel 1 (
    echo Some models failed! Check run_all_models_output.log for details.
) else (
    echo All models completed successfully!
)
echo.
echo Output saved to: run_all_models_output.log
echo ==========================================
