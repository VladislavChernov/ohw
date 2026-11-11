@echo off
:: Dz9 Docker Launch Script
:: This script starts the required Docker containers for dz9
:: and prepares the environment for running the model quality lab.

@echo off
title Dz9 Docker Infrastructure Launcher

:: Check if Docker is running
echo.
echo ============================================
echo Dz9 Infrastructure Launcher
echo ============================================
echo.

:: Check if Docker is running
docker version >nul 2>nul
if %errorlevel% neq 0 (
    echo ERROR: Docker is not running.
    echo Please start Docker Desktop and try again.
    pause
    exit /b 1
)

echo.
echo [Step 1] Starting Ollama infrastructure...
echo.

:: Start the Ollama infrastructure
cd D:\Otus\ohw
docker compose -f dz9\..\infra\compose.yaml up -d

:: Wait for Ollama to be ready
echo.
echo Waiting for Ollama to be ready...
timeout /t 10 /nobreak >nul

:: Check if Ollama is running
docker ps | find "ohw-ollama" >nul
if %errorlevel% neq 0 (
    echo ERROR: Could not start ohw-ollama.
    echo Please check Docker logs and try again.
    pause
    exit /b 1
)

echo.
echo [Step 2] Pulling llama3 model...
echo.

docker exec ohw-ollama ollama pull llama3 2>&1 | find "Status" >nul
echo.
echo [Step 3] Starting dz2 UI container...
echo.

:: Start the dz2 UI container
cd D:\Otus\ohw\dz2
docker compose up -d ui

:: Wait for UI to be ready
timeout /t 5 /nobreak >nul

:: Verify UI is running
docker ps | find "dz2-ui-1" >nul
if %errorlevel% neq 0 (
    echo ERROR: Could not start dz2-ui-1.
    echo Please check Docker logs and try again.
    pause
    exit /b 1
)

echo.
echo ============================================
echo Infrastructure launched successfully!
echo.
echo 1. ohw-ollama is running on port 11434
echo 2. dz2-ui-1 is running on port 8501
echo 3. Llama3 model is being loaded...
echo.
echo You can now run the dz9 pipeline.
echo.
echo To run the dz9 pipeline, use:
echo "  docker compose -f dz2/compose.yaml exec ui python -m model_quality_lab --all"
echo.
echo Press any key to continue...
pause >nul

exit /b 0