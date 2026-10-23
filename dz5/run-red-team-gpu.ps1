# GPU variant: same as run-red-team.ps1, but explicit about the NVIDIA path.
$ErrorActionPreference = "Stop"

& (Join-Path $PSScriptRoot "run-red-team.ps1") @args
exit $LASTEXITCODE
