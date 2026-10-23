# CPU variant: Ollama runs without an NVIDIA GPU. Expect slow responses.
$ErrorActionPreference = "Stop"

& (Join-Path $PSScriptRoot "run-red-team.ps1") -Cpu @args
exit $LASTEXITCODE
