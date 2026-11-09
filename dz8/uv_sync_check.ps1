# Dz8 Verification Script - Part 3: uv sync and CLI Checks
# Save as: D:\Otus\ohw\dz8\uv_sync_check.ps1

Write-Host "=== Dz8 Verification: Step 3: uv sync and CLI Checks ===" -ForegroundColor Cyan

$dz8Path = "D:\Otus\ohw\dz8"
$dz2Path = "D:\Otus\ohw\dz2"
$logPath = "$dz8Path\logs"

# Run uv sync in UI container
Write-Host "Running uv sync in UI container..." -ForegroundColor Gray
docker compose -p ohw -f "$dz2Path\compose.yaml" exec -e UV_PROJECT_ENVIRONMENT=/tmp/dz8-venv ui sh -lc "cd /opt/dz8 && uv sync --frozen --extra ui --dev" 2>&1 | Out-File -FilePath "$logPath\uv_sync.log" -Append

Write-Host "uv sync complete. Check the log above." -ForegroundColor Green

# Run CLI checks
Write-Host "" -ForegroundColor Gray
Write-Host "=== Running CLI Checks ===" -ForegroundColor Cyan

# Health check
Write-Host "Running health check..." -ForegroundColor Gray
docker compose -p ohw -f "$dz2Path\compose.yaml" exec ui python -m llm_degradation health --model llama3 --json 2>&1 | Out-File -FilePath "$logPath\cli_health.log" -Append

# API check
Write-Host "Running api-check..." -ForegroundColor Gray
docker compose -p ohw -f "$dz2Path\compose.yaml" exec ui python -m llm_degradation api-check --model llama3 --json 2>&1 | Out-File -FilePath "$logPath\cli_api_check.log" -Append

# Hallucinations
Write-Host "Running hallucinations (3 runs)..." -ForegroundColor Gray
docker compose -p ohw -f "$dz2Path\compose.yaml" exec ui python -m llm_degradation hallucinations --model llama3 --runs 3 --json 2>&1 | Out-File -FilePath "$logPath\cli_hallucinations.log" -Append

# Consistency
Write-Host "Running consistency (10 runs, seed 42)..." -ForegroundColor Gray
docker compose -p ohw -f "$dz2Path\compose.yaml" exec ui python -m llm_degradation consistency --model llama3 --runs 10 --seed 42 --json 2>&1 | Out-File -FilePath "$logPath\cli_consistency.log" -Append

# Canary
Write-Host "Running canary..." -ForegroundColor Gray
docker compose -p ohw -f "$dz2Path\compose.yaml" exec ui python -m llm_degradation canary --model llama3 --json 2>&1 | Out-File -FilePath "$logPath\cli_canary.log" -Append

# Degradation
Write-Host "Running degradation (5 runs)..." -ForegroundColor Gray
docker compose -p ohw -f "$dz2Path\compose.yaml" exec ui python -m llm_degradation degradation --baseline-model llama3 --candidate-model llama3 --runs 5 --json 2>&1 | Out-File -FilePath "$logPath\cli_degradation.log" -Append

Write-Host "All CLI checks complete." -ForegroundColor Green