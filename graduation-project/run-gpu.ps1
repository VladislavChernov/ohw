#Requires -Version 5.1
<#
.SYNOPSIS
  Run the graduation project with NVIDIA GPU ollama.
.DESCRIPTION
  Thin wrapper around run.ps1 -Gpu. All arguments are passed to the compose app.
  Example: .\run-gpu.ps1 --count 5 --runs 2
#>
$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "run.ps1") -Gpu @args
exit $LASTEXITCODE
