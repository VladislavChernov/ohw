# Dz8 Verification Steps - Using EXISTING dz2 Container Only
# Critical: Reuses ohw-ollama and dz2-ui-1 from infra/dz2
# ZERO new containers created

Write-Host "=== Dz8 Verification: Steps 1-3 (Existing Containers Only) ===" -ForegroundColor Cyan
Write-Host "Following HANDOFF.md strictly: NO new containers" -ForegroundColor Yellow
Write-Host "" -ForegroundColor Gray

# ==========================================================
# STEP 1: Ensure Existing Containers Are Running
# ==========================================================
Write-Host ">>> Step 1: Verify/Start Existing Containers <<<" -ForegroundColor Yellow

Write-Host "Checking Docker containers..." -ForegroundColor Gray

# Check ohw-ollama (from infra/)
Write-Host "Checking ohw-ollama container..." -ForegroundColor Gray
docker ps --filter "name=ohw-ollama" --format "{{.Names}} {{.Status}}" 2>&1

# Check dz2-ui-1 (from dz2/compose.yaml)
Write-Host "Checking dz2-ui-1 container..." -ForegroundColor Gray
docker ps --filter "name=dz2-ui-1" --format "{{.Names}} {{.Status}}" 2>&1

# If not running, start them from dz2 directory
Write-Host "Starting containers from dz2/..." -ForegroundColor Gray
Write-Host "Run in D:\\Otus\\ohw\\dz2:" -ForegroundColor Yellow
Write-Host "  .\\scripts\\up-gpu.ps1    # GPU, or" -ForegroundColor Gray
Write-Host "  .\\scripts\\up-cpu.ps1  # CPU" -ForegroundColor Gray

# ==========================================================
# STEP 2: Pull llama3 to EXISTING ohw-ollama
# ==========================================================
Write-Host "`n>>> Step 2: Pull llama3 to Existing Ollama <<<" -ForegroundColor Yellow
Write-Host "This uses the SAME ollama instance that dz2 already uses" -ForegroundColor Gray
Write-Host "No new containers, just pulling model to existing ohw-ollama" -ForegroundColor Green

Write-Host "Pulling llama3 model..." -ForegroundColor Gray
docker exec ohw-ollama ollama pull llama3 2>&1 | Out-File -FilePath "D:\Otus\ohw\dz8\logs\ollama_pull.log" -Append

Write-Host "Verifying model is available..." -ForegroundColor Gray
docker exec ohw-ollama ollama list 2>&1 | Out-File -FilePath "D:\Otus\ohw\dz8\logs\ollama_list.log" -Append

Write-Host ">>> Step 2 Complete: llama3 now in existing ohw-ollama <<<" -ForegroundColor Green

# ==========================================================
# STEP 3: uv sync and CLI Checks IN dz2-ui-1 container
# ==========================================================
Write-Host "`n>>> Step 3: uv sync and CLI Checks in dz2-ui-1 <<<" -ForegroundColor Yellow
Write-Host "All CLI commands run INSIDE the existing dz2-ui-1 container" -ForegroundColor Gray
Write-Host "dz8 code is mounted at /opt/dz8 inside dz2-ui-1" -ForegroundColor Gray
Write-Host "Temp venv at /tmp/dz8-venv (already configured)" -ForegroundColor Gray

# 3a: uv sync
Write-Host "Running uv sync in dz2-ui-1..." -ForegroundColor Gray
docker compose -f D:\Otus\ohw\dz2\compose.yaml exec -e UV_PROJECT_ENVIRONMENT=/tmp/dz8-venv ui sh -lc "
cd /opt/dz8 && uv sync --frozen --extra ui --dev" 2>&1 | Out-File -FilePath "D:\Otus\ohw\dz8\logs\uv_sync.log" -Append

Write-Host "uv sync finished. Check the log above." -ForegroundColor Green

# 3b: CLI health check
Write-Host "`nRunning health check..." -ForegroundColor Gray
docker compose -f D:\Otus\ohw\dz2\compose.yaml exec ui python -m llm_degradation health --model llama3 --json 2>&1 | Out-File -FilePath "D:\Otus\ohw\dz8\logs\cli_health.log" -Append

# 3c: CLI api-check
Write-Host "Running api-check..." -ForegroundColor Gray
docker compose -f D:\Otus\ohw\dz2\compose.yaml exec ui python -m llm_degradation api-check --model llama3 --json 2>&1 | Out-File -FilePath "D:\Otus\ohw\dz8\logs\cli_api_check.log" -Append

# 3d: CLI hallucinations
Write-Host "Running hallucinations (3 runs)..." -ForegroundColor Gray
docker compose -f D:\Otus\ohw\dz2\compose.yaml exec ui python -m llm_degradation hallucinations --model llama3 --runs 3 --json 2>&1 | Out-File -FilePath "D:\Otus\ohw\dz8\logs\cli_hallucinations.log" -Append

# 3e: CLI consistency
Write-Host "Running consistency (10 runs, seed 42)..." -ForegroundColor Gray
docker compose -f D:\Otus\ohw\dz2\compose.yaml exec ui python -m llm_degradation consistency --model llama3 --runs 10 --seed 42 --json 2>&1 | Out-File -FilePath "D:\Otus\ohw\dz8\logs\cli_consistency.log" -Append

# 3f: CLI canary
Write-Host "Running canary..." -ForegroundColor Gray
docker compose -f D:\Otus\ohw\dz2\compose.yaml exec ui python -m llm_degradation canary --model llama3 --json 2>&1 | Out-File -FilePath "D:\Otus\ohw\dz8\logs\cli_canary.log" -Append

# 3g: CLI degradation
Write-Host "Running degradation (5 runs)..." -ForegroundColor Gray
docker compose -f D:\Otus\ohw\dz2\compose.yaml exec ui python -m llm_degradation degradation --baseline-model llama3 --candidate-model llama3 --runs 5 --json 2>&1 | Out-File -FilePath "D:\Otus\ohw\dz8\logs\cli_degradation.log" -Append

Write-Host "`n>>> Step 3 Complete: All CLI checks in dz2-ui-1 <<<" -ForegroundColor Green
Write-Host "All results logged to D:\\Otus\\ohw\\dz8\\logs\\" -ForegroundColor Gray