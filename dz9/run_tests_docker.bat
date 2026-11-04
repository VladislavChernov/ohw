@echo off
:: Script: run_tests_docker.bat
:: Description: Run model-quality-lab quality gate (ruff, mypy, pytest) in Docker

echo.
echo ==========================================
echo Running model-quality-lab quality gate in Docker
echo ==========================================
echo.
echo Image: ghcr.io/astral-sh/uv:python3.13-bookworm-slim
echo Project: D:\Otus\ohw
echo.

docker run --rm -v "D:/Otus/ohw:/app" -w /app "ghcr.io/astral-sh/uv:python3.13-bookworm-slim" sh -c "uv pip install --system -r dz9/requirements.txt && echo '--- CLEANING UNUSED CODE ---' && ruff check dz9/src/ --fix --unsafe-fixes --ignore EXE002 && echo '--- RUNNING RUFF LINTER ---' && ruff check dz9/src/ --ignore EXE002 && echo '--- RUNNING MYPY TYPE CHECK ---' && mypy dz9/src/ --ignore-missing-imports && echo '--- RUNNING PYTEST ---' && python -m pytest dz9/tests/test_basic.py -v -p no:cacheprovider"

echo.
if errorlevel 1 (
    echo Quality gate FAILED! Check test_output.log for details.
) else (
    echo Quality gate passed!
)
echo.
echo Output saved to: test_output.log
echo ==========================================
