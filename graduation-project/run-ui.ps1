#Requires -Version 5.1
<#
.SYNOPSIS
  Start the Streamlit workbench from the local Python environment.
.DESCRIPTION
  Thin wrapper around: uv run python -m src.ui
  Opens http://localhost:8501 by default. Override with
  $env:GRADUATION_UI_HOST and $env:GRADUATION_UI_PORT.
#>
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
& uv run python -m src.ui
exit $LASTEXITCODE
