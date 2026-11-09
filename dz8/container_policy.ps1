# Dz8 Verification Scripts - Container Reuse Policy
# Critical: These scripts ONLY reuse existing containers per HANDOFF.md
# NO new permanent containers are created for Dz8

Write-Host "=== Dz8 Container Reuse Policy ===" -ForegroundColor Cyan
Write-Host "" -ForegroundColor Gray
Write-Host "HANDOFF.md strictly prohibits:" -ForegroundColor Red
Write-Host "  · New permanent containers for Dz8" -ForegroundColor Red
Write-Host "  · dz8/Dockerfile" -ForegroundColor Red
Write-Host "  · dz8/compose.yaml" -ForegroundColor Red
Write-Host "  · New service/port 8502" -ForegroundColor Red
Write-Host "  · Second Ollama instance" -ForegroundColor Red
Write-Host "" -ForegroundColor Gray
Write-Host "Scripts ONLY reuse:" -ForegroundColor Green
Write-Host "  · ohw-ollama from infra/ (existing)" -ForegroundColor Green
Write-Host "  · dz2-ui-1 on port 8501 (existing)" -ForegroundColor Green
Write-Host "  · ohw-python:3.13 from infra/python (existing)" -ForegroundColor Green
Write-Host "" -ForegroundColor Gray
Write-Host "Key Commands (no new containers):" -ForegroundColor Yellow
Write-Host "  docker exec ohw-ollama ollama pull llama3" -ForegroundColor Gray
Write-Host "  docker compose -f dz2/compose.yaml exec ui ..." -ForegroundColor Gray
Write-Host "  docker compose -f dz2/compose.yaml up -d --no-build ui" -ForegroundColor Gray
Write-Host "" -ForegroundColor Gray
Write-Host "Violating these rules = DoD failure!" -ForegroundColor Red
Write-Host "All scripts below follow this policy strictly." -ForegroundColor Green