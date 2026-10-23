# Cross-platform PowerShell launcher for the dz5 red-team run.
# Works on Windows PowerShell 5.1+ and PowerShell 7+ on Linux/macOS.
# Default is GPU; use -Cpu on a machine without an NVIDIA GPU.
param(
    [ValidateSet(1, 2, 3, 4, 5)]
    [int]$Preset = 1,
    [int]$Runs = 3,
    [string[]]$Scenario = @(),
    [double]$Timeout = 120,
    [string]$Target = "",
    [switch]$Augustus,
    [switch]$Cpu,
    [switch]$NoUp,
    [switch]$NoBuild,
    [switch]$NoReport,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$Root = $PSScriptRoot
$ComposeFile = Join-Path $Root "infra/compose.yaml"
$GpuFile = Join-Path $Root "infra/compose.gpu.yaml"
$ReportsDir = Join-Path $Root "reports"
$ComposeFiles = @($ComposeFile)
if (-not $Cpu) {
    $ComposeFiles += $GpuFile
}

if ($Runs -lt 1) {
    [Console]::Error.WriteLine("error: -Runs must be at least 1")
    exit 2
}
if ($Timeout -le 0) {
    [Console]::Error.WriteLine("error: -Timeout must be positive")
    exit 2
}
if (-not $Cpu -and -not (Test-Path -LiteralPath $GpuFile)) {
    [Console]::Error.WriteLine("error: GPU overlay not found: $GpuFile")
    exit 2
}

if ([string]::IsNullOrEmpty($Target)) {
    if ($Preset -eq 5) {
        $Target = "http://agent-fixed:8080"
    } else {
        $Target = "http://agent:8080"
    }
}

$builtinGuards = if (@(2, 4) -contains $Preset) { "on" } else { "off" }
$externalGuard = if (@(3, 4) -contains $Preset) { "on" } else { "off" }
$script:GuardEnv = @{
    BUILTIN_GUARDS = $builtinGuards
    EXTERNAL_GUARD = $externalGuard
}

function Invoke-Compose {
    param([string[]]$Arguments)

    $command = @("compose")
    foreach ($file in $ComposeFiles) {
        $command += @("-f", $file)
    }
    $command += @("--profile", "attack")
    $command += $Arguments
    if ($DryRun) {
        Write-Host ("+ docker " + ($command -join " "))
        return 0
    }

    $saved = @{}
    foreach ($name in $script:GuardEnv.Keys) {
        $saved[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
        [Environment]::SetEnvironmentVariable($name, $script:GuardEnv[$name], "Process")
    }
    try {
        & docker @command | Out-Host
        return $LASTEXITCODE
    } finally {
        foreach ($name in $saved.Keys) {
            [Environment]::SetEnvironmentVariable($name, $saved[$name], "Process")
        }
    }
}

if (-not $DryRun) {
    if ($null -eq (Get-Command docker -ErrorAction SilentlyContinue)) {
        [Console]::Error.WriteLine("error: docker not found in PATH")
        exit 127
    }
    & docker compose version *> $null
    if ($LASTEXITCODE -ne 0) {
        [Console]::Error.WriteLine("error: docker compose is not available")
        exit 127
    }
}

$runArguments = @("run", "--rm")
if (-not $NoBuild) {
    $runArguments += "--build"
}
$timeoutText = $Timeout.ToString([System.Globalization.CultureInfo]::InvariantCulture)
$augustusValue = if ($Augustus) { "on" } else { "off" }
$runArguments += @(
    "--entrypoint", "python", "attack-runner",
    "-m", "attack_runner", "run",
    "--target", $Target,
    "--preset", "$Preset",
    "--runs", "$Runs",
    "--timeout", $timeoutText,
    "--augustus", $augustusValue
)
foreach ($item in $Scenario) {
    $runArguments += @("--scenario", $item)
}

$reportArguments = @(
    "run", "--rm", "--no-deps",
    "--entrypoint", "python", "attack-runner",
    "-m", "report_builder", "build",
    "--run", "/reports/<run-id>"
)

if ($DryRun) {
    if (-not $NoUp) {
        $null = Invoke-Compose @("up", "-d", "ollama", "agent", "agent-fixed", "wall", "ui", "db-admin")
    }
    $null = Invoke-Compose $runArguments
    if (-not $NoReport) {
        $null = Invoke-Compose $reportArguments
    }
    exit 0
}

if (-not $NoUp) {
    $code = Invoke-Compose @("up", "-d", "ollama", "agent", "agent-fixed", "wall", "ui", "db-admin")
    if ($code -ne 0) {
        exit $code
    }
}

New-Item -ItemType Directory -Force -Path $ReportsDir | Out-Null
$before = @(
    Get-ChildItem -LiteralPath $ReportsDir -Directory -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty Name
)

$code = Invoke-Compose $runArguments
if ($code -ne 0) {
    exit $code
}

$after = @(
    Get-ChildItem -LiteralPath $ReportsDir -Directory -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty Name
)
$newRuns = @($after | Where-Object { $before -notcontains $_ } | Sort-Object)
if ($newRuns.Count -eq 0) {
    [Console]::Error.WriteLine("error: no new run directory found under reports/")
    exit 1
}
$runId = $newRuns[-1]

if (-not $NoReport) {
    $reportArguments[-1] = "/reports/$runId"
    $code = Invoke-Compose $reportArguments
    if ($code -ne 0) {
        exit $code
    }
}

Write-Host ""
Write-Host ("run:    " + (Join-Path $ReportsDir $runId))
if ($NoReport) {
    Write-Host "report: skipped (-NoReport)"
} else {
    Write-Host ("report: " + (Join-Path $ReportsDir "red-team.md"))
}
if ($Augustus) {
    Write-Host "augustus: http://localhost:8090/augustus"
}
Write-Host "ui:       http://localhost:8090/"
