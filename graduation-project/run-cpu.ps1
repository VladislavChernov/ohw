#Requires -Version 5.1
<#
.SYNOPSIS
  Run the graduation project with CPU ollama.
.DESCRIPTION
  Thin wrapper around run.ps1. All arguments are passed to the compose app.
  Example: .\run-cpu.ps1 --count 5 --runs 2
#>
$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "run.ps1") @args
exit $LASTEXITCODE
