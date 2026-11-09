# Dz8 Verification Script - Part 4: UI Checks
# Save as: D:\Otus\ohw\dz8\ui_checks.ps1

Write-Host "=== Dz8 Verification: Step 4: UI Checks ===" -ForegroundColor Cyan

$dz2Path = "D:\Otus\ohw\dz2"
$dz8Path = "D:\Otus\ohw\dz8"
$logPath = "$dz8Path\logs"

# Check UI status
Write-Host "Checking UI container status..." -ForegroundColor Gray
docker compose -p ohw -f "$dz2Path\compose.yaml" ps ui 2>&1 | Out-File -FilePath "$logPath\ui_status.log" -Append

# Ruff check
Write-Host "Running ruff check..." -ForegroundColor Gray
docker compose -p ohw -f "$dz2Path\compose.yaml" exec ui sh -lc "cd /opt/dz8 && /tmp/dz8-venv/bin/ruff check src tests" 2>&1 | Out-File -FilePath "$logPath\ruff_check.log" -Append

# MyPy check
Write-Host "Running mypy check..." -ForegroundColor Gray
docker compose -p ohw -f "$dz2Path\compose.yaml" exec ui sh -lc "cd /opt/dz8 && /tmp/dz8-venv/bin/mypy src" 2>&1 | Out-File -FilePath "$logPath\mypy_check.log" -Append

Write-Host "UI checks complete." -ForegroundColor Green