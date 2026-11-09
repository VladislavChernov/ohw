# Dz8 Verification Script - Part 1: Setup and Container Start
# Save as: D:\Otus\ohw\dz8\setup_containers.ps1

Write-Host "=== Dz8 Verification: Step 1: Start Containers ===" -ForegroundColor Cyan

$dz2Path = "D:\Otus\ohw\dz2"
$dz8Path = "D:\Otus\ohw\dz8"
$logPath = "$dz8Path\logs"

if (-not (Test-Path $logPath)) { New-Item -ItemType Directory -Path $logPath | Out-Null }

# Check docker
Write-Host "Checking Docker daemon..." -ForegroundColor Gray
try { docker ps > $null; Write-Host "Docker running." -ForegroundColor Green }
catch { Write-Host "ERROR: Docker not running!" -ForegroundColor Red; return }

# Run GPU start script
Write-Host "Running: .\scripts\up-gpu.ps1" -ForegroundColor Gray
& "$dz2Path\scripts\up-gpu.ps1" 2>&1 | Out-File -FilePath "$logPath\docker_start.log" -Append

Start-Sleep -Seconds 5

# Verify containers
Write-Host "Checking container status:" -ForegroundColor Gray
docker ps --format "{{.Names}} {{.Image}}" | Out-File -FilePath "$logPath\docker_ps.log" -Append
Write-Host "Containers checked." -ForegroundColor Green