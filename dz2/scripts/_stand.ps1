# Shared stand launcher for the dz2 project (Windows / PowerShell).
# Do not call directly: use up-cpu.ps1 or up-gpu.ps1.
#
# What it does:
#   1. checks Docker (and NVIDIA for gpu mode);
#   2. builds the shared base image ohw-python:3.13 if it is missing;
#   3. starts the shared components declared in ../infra.yaml (ollama),
#      with the infra GPU overlay in gpu mode;
#   4. builds the single project image ohw/dz2:local (ui extra included);
#   5. starts the ui service and waits for it.
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("cpu", "gpu")]
    [string]$Mode,

    [switch]$Rebuild
)

$ErrorActionPreference = "Stop"

$scriptsDir = $PSScriptRoot
$projectDir = Split-Path $scriptsDir -Parent
$repoDir = Split-Path $projectDir -Parent
$infraDir = Join-Path $repoDir "infra"
$composeFile = Join-Path $projectDir "compose.yaml"
$manifest = Join-Path $projectDir "infra.yaml"
$baseImage = "ohw-python:3.13"
$projectImage = "ohw/dz2:local"
$uiUrl = "http://localhost:8501"

function Invoke-Compose {
    param([string[]]$ComposeArgs)
    & docker compose @ComposeArgs
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose failed: $($ComposeArgs -join ' ')"
    }
}

function Assert-Command {
    param([string]$Name, [string]$Hint)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name not found. $Hint"
    }
}

function Get-Components {
    if (-not (Test-Path $manifest)) {
        throw "infra.yaml not found: $manifest"
    }
    $components = Select-String -Path $manifest -Pattern '^\s*-\s*([A-Za-z0-9_-]+)\s*$' |
        ForEach-Object { $_.Matches[0].Groups[1].Value }
    if (-not $components) {
        throw "No components declared in $manifest"
    }
    return $components
}

function Wait-Healthy {
    param(
        [string]$Container,
        [string]$Label,
        [int]$TimeoutSeconds = 360
    )
    Write-Host "Waiting for $Label ($Container)..."
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $status = & docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' $Container 2>$null
        if ($LASTEXITCODE -eq 0 -and $status -eq "healthy") {
            Write-Host "  $Label is healthy"
            return
        }
        Start-Sleep -Seconds 2
    }
    throw "$Label did not become healthy in $TimeoutSeconds s. Check: docker logs $Container"
}

function Wait-Http {
    param(
        [string]$Url,
        [string]$Label,
        [int]$TimeoutSeconds = 180
    )
    Write-Host "Waiting for $Label ($Url)..."
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3
            if ($response.StatusCode -eq 200) {
                Write-Host "  $Label is up"
                return
            }
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    throw "$Label did not answer in $TimeoutSeconds s. Check: docker compose -f `"$composeFile`" logs ui"
}

Write-Host "=== dz2 stand: $Mode ==="

Assert-Command -Name "docker" -Hint "Install Docker Desktop and start it."
& docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker daemon is not available. Start Docker Desktop."
}

if ($Mode -eq "gpu") {
    Assert-Command -Name "nvidia-smi" -Hint "Install the NVIDIA driver for the GPU stand."
    & nvidia-smi -L *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "nvidia-smi cannot see a GPU."
    }
    $runtimes = & docker info --format '{{json .Runtimes}}'
    if ($runtimes -notmatch '"nvidia"') {
        throw "Docker has no nvidia runtime. Install NVIDIA Container Toolkit, then restart Docker Desktop."
    }
}

& docker image inspect $baseImage *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Building shared base image $baseImage (infra/python)..."
    & docker build -t $baseImage (Join-Path $infraDir "python")
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to build $baseImage"
    }
}

$components = Get-Components
$infraArgs = @("-f", (Join-Path $infraDir "compose.yaml"))
if ($Mode -eq "gpu") {
    $infraArgs += @("-f", (Join-Path $infraDir "compose.gpu.yaml"))
}
foreach ($component in $components) {
    $infraArgs += @("--profile", $component)
}
$infraArgs += @("up", "-d", "--force-recreate")
Write-Host "Starting shared components: $($components -join ' ')"
Invoke-Compose $infraArgs

if ($Rebuild) {
    Write-Host "Removing old project image $projectImage..."
    & docker rmi --force $projectImage *> $null
}

Write-Host "Building project image $projectImage..."
& docker build --build-arg "EXTRAS=--extra ui" -t $projectImage $projectDir
if ($LASTEXITCODE -ne 0) {
    throw "Failed to build $projectImage"
}

Write-Host "Starting the ui service..."
Invoke-Compose @("-f", $composeFile, "up", "-d", "--no-build", "ui")

Wait-Healthy -Container "ohw-ollama" -Label "ollama"
Wait-Http -Url "$uiUrl/_stcore/health" -Label "dz2 ui"

Write-Host ""
Write-Host "Stand is up ($Mode):"
Write-Host "  UI:      $uiUrl"
Write-Host "  Ollama:  http://localhost:11434 (container ohw-ollama)"
Write-Host "  CLI:     docker compose -f `"$composeFile`" run --rm app --count 10"
Write-Host "  Stop:    .\scripts\down.ps1 [-Infra]"
