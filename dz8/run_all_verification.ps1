# Dz8 Complete Verification Script with Status Checking
# Save as: D:\Otus\ohw\dz8\run_all_verification.ps1

Write-Host ("=" * 60) -ForegroundColor Cyan
Write-Host "Dz8 Complete Verification & Setup Script (Strict Mode)"
Write-Host "Based on: HANDOFF.md and current_state.md"
Write-Host ("=" * 60) -ForegroundColor Cyan

$dz8Path = "D:\Otus\ohw\dz8"
$dz2Path = "D:\Otus\ohw\dz2"
$logPath = "$dz8Path\logs"
$venvPath = "/tmp/dz8-venv"

# Хранилище статусов для финального отчета
$Results = [ordered]@{
    "Docker Containers" = "Not Started"
    "Ollama Model Pull" = "Skipped"
    "uv sync"           = "Skipped"
    "CLI: health"       = "Skipped"
    "CLI: api-check"    = "Skipped"
    "CLI: hallucinations" = "Skipped"
    "CLI: consistency"  = "Skipped"
    "CLI: canary"       = "Skipped"
    "CLI: degradation"  = "Skipped"
    "Linter: ruff"      = "Skipped"
    "Type-Check: mypy"  = "Skipped"
}

$script:GateExitCode = 0

# Функция для логирования и проверки кода возврата
function Check-Command-Success {
    param (
        [string]$StepName,
        [int]$ExitCode,
        [bool]$Critical = $false
    )
    if ($ExitCode -eq 0) {
        Write-Host "  [OK] ${StepName}: SUCCESS" -ForegroundColor Green
        $script:Results[$StepName] = "PASS"
    } else {
        Write-Host "  [FAIL] ${StepName}: FAILED (Exit Code: $ExitCode)" -ForegroundColor Red
        $script:Results[$StepName] = "FAIL"
        if ($Critical) {
            Write-Host "`n[CRITICAL ERROR] Прерываем выполнение скрипта, так как данный шаг обязателен!" -ForegroundColor Red
            Show-Summary
            exit $ExitCode
        }
    }
}

function Show-Summary {
    Write-Host ("`n" + "=" * 60) -ForegroundColor Cyan
    Write-Host "ФИНАЛЬНЫЙ ОТЧЕТ О ПРОВЕРКЕ" -ForegroundColor Cyan
    Write-Host ("=" * 60) -ForegroundColor Cyan
    
    $allPassed = $true
    foreach ($key in $Results.Keys) {
        $status = $Results[$key]
        if ($status -eq "PASS") {
            Write-Host "  [OK] ${key}: PASSED" -ForegroundColor Green
        } elseif ($status -eq "FAIL") {
            Write-Host "  [FAIL] ${key}: FAILED" -ForegroundColor Red
            $allPassed = $false
        } else {
            Write-Host "  [SKIP] ${key}: $status" -ForegroundColor Gray
        }
    }
    
    Write-Host ("=" * 60) -ForegroundColor Cyan
    if ($allPassed) {
        Write-Host "Пройдены ВСЕ проверки! Фаза 1 успешно завершена. Переходите к Фазе 2." -ForegroundColor Green
    } else {
        $script:GateExitCode = 1
        Write-Host "Некоторые проверки завершились ОШИБКОЙ. Изучите логи в: $logPath" -ForegroundColor Yellow
    }
}

# Создаем папку логов
if (-not (Test-Path $logPath)) {
    New-Item -ItemType Directory -Path $logPath | Out-Null
}

# Очищаем старые логи
Get-ChildItem -Path $logPath -Filter "*.log" | Remove-Item -Force

# ==========================================================
# Step 1: Start Containers
# ==========================================================
Write-Host "`n--- Step 1: Starting Docker Containers ---" -ForegroundColor Yellow
& "$dz2Path\scripts\up-gpu.ps1" 2>&1 | Out-File -FilePath "$logPath\docker_start.log" -Append
Check-Command-Success -StepName "Docker Containers" -ExitCode $LastExitCode -Critical $true

Write-Host "Waiting for services to initialize (20s)..." -ForegroundColor Gray
Start-Sleep -Seconds 20
docker ps --format "{{.Names}} {{.Image}}" | Out-File -FilePath "$logPath\docker_ps.log" -Append

# ==========================================================
# Step 2: Pull llama3 Model
# ==========================================================
Write-Host "`n--- Step 2: Pulling llama3 Model ---" -ForegroundColor Yellow
docker exec ohw-ollama ollama pull llama3 2>&1 | Out-File -FilePath "$logPath\ollama_pull.log" -Append
$pullStatus = $LastExitCode
docker exec ohw-ollama ollama list 2>&1 | Out-File -FilePath "$logPath\ollama_list.log" -Append
Check-Command-Success -StepName "Ollama Model Pull" -ExitCode $pullStatus -Critical $true

# ==========================================================
# Step 3: uv sync and CLI Checks
# ==========================================================
Write-Host "`n--- Step 3: uv sync and CLI Checks ---" -ForegroundColor Yellow

# uv sync
Write-Host "Running uv sync..." -ForegroundColor Gray
docker compose -f "$dz2Path\compose.yaml" exec -e UV_PROJECT_ENVIRONMENT=$venvPath -e UV_CACHE_DIR=/tmp/uv-cache ui sh -lc "cd /opt/dz8 && uv sync --frozen --extra ui --dev" 2>&1 | Out-File -FilePath "$logPath\uv_sync.log" -Append
Check-Command-Success -StepName "uv sync" -ExitCode $LastExitCode -Critical $true

# CLI checks
Write-Host "Running CLI tool checks..." -ForegroundColor Gray

docker compose -f "$dz2Path\compose.yaml" exec ui $venvPath/bin/python -m llm_degradation health --model llama3 --json 2>&1 | Out-File -FilePath "$logPath\cli_health.log" -Append
Check-Command-Success -StepName "CLI: health" -ExitCode $LastExitCode

docker compose -f "$dz2Path\compose.yaml" exec ui $venvPath/bin/python -m llm_degradation api-check --model llama3 --json 2>&1 | Out-File -FilePath "$logPath\cli_api_check.log" -Append
Check-Command-Success -StepName "CLI: api-check" -ExitCode $LastExitCode

docker compose -f "$dz2Path\compose.yaml" exec ui $venvPath/bin/python -m llm_degradation hallucinations --model llama3 --runs 3 --input /opt/dz8/golden/inputs/catalog.md --json 2>&1 | Out-File -FilePath "$logPath\cli_hallucinations.log" -Append
Check-Command-Success -StepName "CLI: hallucinations" -ExitCode $LastExitCode

docker compose -f "$dz2Path\compose.yaml" exec ui $venvPath/bin/python -m llm_degradation consistency --model llama3 --runs 10 --seed 42 --input /opt/dz8/golden/inputs/catalog.md --json 2>&1 | Out-File -FilePath "$logPath\cli_consistency.log" -Append
Check-Command-Success -StepName "CLI: consistency" -ExitCode $LastExitCode

docker compose -f "$dz2Path\compose.yaml" exec ui $venvPath/bin/python -m llm_degradation canary --model llama3 --input /opt/dz8/golden/inputs/catalog.md --json 2>&1 | Out-File -FilePath "$logPath\cli_canary.log" -Append
Check-Command-Success -StepName "CLI: canary" -ExitCode $LastExitCode

docker compose -f "$dz2Path\compose.yaml" exec ui $venvPath/bin/python -m llm_degradation degradation --baseline-model llama3 --candidate-model llama3 --runs 5 --input /opt/dz8/golden/inputs/catalog.md --json 2>&1 | Out-File -FilePath "$logPath\cli_degradation.log" -Append
Check-Command-Success -StepName "CLI: degradation" -ExitCode $LastExitCode

# ==========================================================
# Step 4: UI and Code Quality Checks
# ==========================================================
Write-Host "`n--- Step 4: UI and Code Quality Checks ---" -ForegroundColor Yellow

docker compose -f "$dz2Path\compose.yaml" ps ui 2>&1 | Out-File -FilePath "$logPath\ui_status.log" -Append

docker compose -f "$dz2Path\compose.yaml" exec ui sh -lc "cd /opt/dz8 && $venvPath/bin/ruff check --no-cache src tests" 2>&1 | Out-File -FilePath "$logPath\ruff_check.log" -Append
Check-Command-Success -StepName "Linter: ruff" -ExitCode $LastExitCode

docker compose -f "$dz2Path\compose.yaml" exec ui sh -lc "cd /opt/dz8 && $venvPath/bin/mypy --cache-dir=/tmp/mypy-cache src" 2>&1 | Out-File -FilePath "$logPath\mypy_check.log" -Append
Check-Command-Success -StepName "Type-Check: mypy" -ExitCode $LastExitCode

# Вывод итогов
Show-Summary
exit $script:GateExitCode

