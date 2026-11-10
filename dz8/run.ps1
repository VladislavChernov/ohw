# One-command launcher for the LLM Degradation Lab (Windows).
#
# Starts the shared dz2 stand (ohw-ollama + dz2-ui-1), makes sure llama3 is
# present, optionally runs the lab health checks and prints the UI URL.
# The lab itself lives inside the dz2 UI:
#   http://localhost:8501 -> sidebar -> "Model check".
#
# No new containers, ports or networks are created: the script reuses the dz2
# stand scripts from the sibling dz2 folder and the shared infra/ catalog.
#
# Usage:
#   .\run.ps1                 # auto: GPU when Docker exposes the nvidia runtime, else CPU
#   .\run.ps1 -Mode gpu       # force the GPU stand
#   .\run.ps1 -Mode cpu       # force the CPU stand
#   .\run.ps1 -Rebuild        # rebuild the project image
#   .\run.ps1 -Check          # also run health + api-check against llama3
#   .\run.ps1 -NoPull         # do not pull llama3 (offline demo)
param(
    [ValidateSet("auto", "gpu", "cpu")]
    [string]$Mode = "auto",

    [switch]$Rebuild,
    [switch]$Check,
    [switch]$NoPull
)

$ErrorActionPreference = "Stop"

$dz8Dir = $PSScriptRoot
$repoRoot = Split-Path $dz8Dir -Parent
$dz2Dir = Join-Path $repoRoot "dz2"
$dz2Scripts = Join-Path $dz2Dir "scripts"
$composeFile = Join-Path $dz2Dir "compose.yaml"
$infraCompose = Join-Path (Join-Path $repoRoot "infra") "compose.yaml"
$model = "llama3"
$uiUrl = "http://localhost:8501"

function Invoke-External {
    # Native tools (docker, nvidia-smi) write progress to stderr. Under
    # ErrorActionPreference = Stop PowerShell 5.1 turns that into a terminating
    # error, so relax the preference for the call and decide by the exit code.
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string[]]$Arguments = @(),
        [switch]$Quiet
    )
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        if ($Quiet) {
            & $Name @Arguments *> $null
        } else {
            & $Name @Arguments | Out-Host
        }
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
    }
    return $code
}

function Get-ExternalOutput {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string[]]$Arguments = @()
    )
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        return (& $Name @Arguments 2>$null | Out-String)
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Test-GpuRuntime {
    if (-not (Get-Command nvidia-smi -ErrorAction SilentlyContinue)) {
        return $false
    }
    if ((Invoke-External -Name "nvidia-smi" -Arguments @("-L") -Quiet) -ne 0) {
        return $false
    }
    $runtimes = Get-ExternalOutput -Name "docker" -Arguments @("info", "--format", "{{json .Runtimes}}")
    return ($runtimes -match '"nvidia"')
}

function Test-StandHealthy {
    $ollama = Get-ExternalOutput -Name "docker" -Arguments @(
        "inspect", "ohw-ollama", "--format",
        "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}"
    )
    if ($ollama.Trim() -ne "healthy") {
        return $false
    }
    $ui = Get-ExternalOutput -Name "docker" -Arguments @(
        "inspect", "dz2-ui-1", "--format", "{{.State.Status}}"
    )
    if ($ui.Trim() -ne "running") {
        return $false
    }
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "$uiUrl/_stcore/health" -TimeoutSec 3
        return ($response.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Get-StandMode {
    $requests = Get-ExternalOutput -Name "docker" -Arguments @(
        "inspect", "ohw-ollama", "--format", "{{json .HostConfig.DeviceRequests}}"
    )
    if ($requests -match '"Driver":"nvidia"') {
        return "gpu"
    }
    return "cpu"
}

Write-Host "=== dz8 lab launcher ==="

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "docker not found. Install Docker Desktop and start it."
}

if (-not (Test-Path $composeFile) -or -not (Test-Path $infraCompose)) {
    throw ("The lab needs the dz2 and infra folders next to dz8. " +
        "Restore them with: git sparse-checkout set dz8 dz2 infra")
}

if ((Invoke-External -Name "docker" -Arguments @("info") -Quiet) -ne 0) {
    throw "Docker daemon is not available. Start Docker Desktop."
}

if ($Mode -eq "auto") {
    if (Test-GpuRuntime) {
        $Mode = "gpu"
    } else {
        $Mode = "cpu"
    }
    Write-Host "Detected stand mode: $Mode"
}

$upScript = Join-Path $dz2Scripts ("up-{0}.ps1" -f $Mode)
if (-not (Test-Path $upScript)) {
    throw "Stand script not found: $upScript"
}

if ((Test-StandHealthy) -and (Get-StandMode) -eq $Mode -and -not $Rebuild) {
    Write-Host "The stand is already up ($Mode); skipping the dz2 up-script (use -Rebuild to restart)."
} else {
    $upArgs = @()
    if ($Rebuild) {
        $upArgs += "-Rebuild"
    }
    & $upScript @upArgs
    if ($LASTEXITCODE -gt 0) {
        throw "The stand failed to start (exit $LASTEXITCODE)"
    }
}

if ($NoPull) {
    Write-Host "Skipping the model check (-NoPull)."
} else {
    $installed = Get-ExternalOutput -Name "docker" -Arguments @(
        "exec", "ohw-ollama", "ollama", "list"
    )
    if ($installed -match "(?m)^\s*llama3") {
        Write-Host "Model $model is already installed in ohw-ollama."
    } else {
        Write-Host "Pulling $model into ohw-ollama (one-time download, several GB)..."
        $pullCode = Invoke-External -Name "docker" -Arguments @(
            "exec", "ohw-ollama", "ollama", "pull", $model
        )
        if ($pullCode -ne 0) {
            throw "ollama pull $model failed (exit $pullCode)"
        }
    }
}

$checkCode = 0
if ($Check) {
    Write-Host "Checking the lab against $model (the first call loads the model)..."
    foreach ($command in @("health", "api-check")) {
        $code = Invoke-External -Name "docker" -Arguments @(
            "compose", "-f", $composeFile, "exec", "-T", "ui",
            "python", "-m", "llm_degradation", $command, "--model", $model, "--json"
        )
        if ($code -ne 0) {
            Write-Host "  $command failed (exit $code)"
            $checkCode = 1
        } else {
            Write-Host "  $command ok"
        }
    }
}

Write-Host ""
Write-Host "The lab is ready:"
Write-Host "  UI:      $uiUrl  (sidebar -> 'Model check')"
Write-Host "  Model:   $model"
Write-Host "  Output:  $(Join-Path $dz8Dir 'output')"
Write-Host "  Docs:    $(Join-Path $dz8Dir 'docs\user-guide.md')"
Write-Host "  Stop:    $(Join-Path $dz2Scripts 'down.ps1') [-Infra]"
Write-Host ""
Write-Host "The first answer can take 2-3 minutes while the model is loaded."

exit $checkCode
