# Dz8 Phase 1: Existing Container Verification
# Containers already running, llama3 already pulled
# CRITICAL: Use /tmp/dz8-venv/bin/python for ALL commands

Write-Host "=== Dz8 Phase 1: Verification ===" -ForegroundColor Cyan
Write-Host "Containers: Already running (ohw-ollama + dz2-ui-1)" -ForegroundColor Yellow
Write-Host "Model: llama3 already downloaded" -ForegroundColor Yellow
Write-Host "Task: uv sync + CLI via /tmp/dz8-venv/bin/python" -ForegroundColor Yellow
Write-Host "" -ForegroundColor Gray

$dz2Path = "D:\Otus\ohw\dz2"
$logPath = "D:\Otus\ohw\dz8\logs"
$venvPath = "/tmp/dz8-venv"

if (-not (Test-Path $logPath)) { New-Item -ItemType Directory -Path $logPath | Out-Null }
Get-ChildItem -Path $logPath -Filter "*.log" | Remove-Item -Force

# STEP 1: uv sync
Write-Host "`n--- Step 1: uv sync ---" -ForegroundColor Yellow
Write-Host "Синхронизируем зависимости пакетов..." -ForegroundColor Gray
# Добавлен параметр UV_CACHE_DIR=/tmp/uv-cache для исправления ошибки Permission Denied
docker compose -f "$dz2Path\compose.yaml" exec -e UV_PROJECT_ENVIRONMENT=$venvPath -e UV_CACHE_DIR=/tmp/uv-cache ui sh -lc "
cd /opt/dz8 && uv sync --extra ui --dev" 2>&1 | Out-File -FilePath "$logPath\uv_sync.log" -Append

# STEP 2: CLI commands via venv (Исправлено на хэш-таблицу для нормальных имён файлов)
Write-Host "`n--- Step 2: CLI via venv python ---" -ForegroundColor Yellow

$commands = [ordered]@{
    "health"         = "health --model llama3 --json"
    "api_check"      = "api-check --model llama3 --json" 
    "hallucinations" = "hallucinations --model llama3 --runs 3 --json"
    "consistency"    = "consistency --model llama3 --runs 10 --seed 42 --json"
    "canary"         = "canary --model llama3 --json"
    "degradation"    = "degradation --baseline-model llama3 --candidate-model llama3 --runs 5 --json"
}

foreach ($testName in $commands.Keys) {
    $args = $commands[$testName]
    Write-Host "Running $testName check... (Please wait)" -ForegroundColor Gray
    docker compose -f "$dz2Path\compose.yaml" exec ui $venvPath/bin/python -m llm_degradation $args 2>&1 | Out-File -FilePath "$logPath\cli_$testName.log" -Append
}

# STEP 3: ruff and mypy (Исправлен вызов утилит напрямую из папки bin виртуального окружения)
Write-Host "`n--- Step 3: ruff + mypy via venv ---" -ForegroundColor Yellow
Write-Host "Running Ruff linter..." -ForegroundColor Gray
docker compose -f "$dz2Path\compose.yaml" exec ui sh -lc "cd /opt/dz8 && $venvPath/bin/ruff check src tests" 2>&1 | Out-File -FilePath "$logPath\ruff_check.log" -Append

Write-Host "Running MyPy type checker..." -ForegroundColor Gray
docker compose -f "$dz2Path\compose.yaml" exec ui sh -lc "cd /opt/dz8 && $venvPath/bin/mypy src" 2>&1 | Out-File -FilePath "$logPath\mypy_check.log" -Append

Write-Host "`nVerification complete. Check $logPath\* files." -ForegroundColor Green
