# Dz8 Verification Script - Part 2: Pull llama3 Model
# Save as: D:\Otus\ohw\dz8\pull_model.ps1

Write-Host "=== Dz8 Verification: Step 2: Pull llama3 Model ===" -ForegroundColor Cyan

$logPath = "D:\Otus\ohw\dz8\logs"

# Pull llama3 model
Write-Host "Pulling llama3 to ohw-ollama..." -ForegroundColor Gray
docker exec ohw-ollama ollama pull llama3 2>&1 | Out-File -FilePath "$logPath\ollama_pull.log" -Append

# List models
Write-Host "Available models:" -ForegroundColor Gray
docker exec ohw-ollama ollama list 2>&1 | Out-File -FilePath "$logPath\ollama_list.log" -Append

Write-Host "Model pull complete." -ForegroundColor Green