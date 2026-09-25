<#
.SYNOPSIS
    Фоновый сборщик ресурсов стенда eval (пик RAM/VRAM недоступен изнутри контейнера).

.DESCRIPTION
    Раннер не видит docker stats (нет docker socket), поэтому пик памяти снимает
    хостовый wrapper: этот скрипт в цикле пишет JSONL-сэмплы и завершается, когда
    появляется стоп-файл. Каждый сэмпл — одна строка JSON:
      { ts, host: {...}, gpu: {...}, containers: { name: bytes } }

    Скрипт НЕ вызывает docker compose up/down и не меняет состояние стенда.

.EXAMPLE
    .\eval_sample_resources.ps1 -Project ohw-eval-minimal -IntervalSec 5 `
        -Out C:\Temp\samples.jsonl -StopFile C:\Temp\samples.stop
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Project,
    [Parameter(Mandatory = $true)][string]$Out,
    [Parameter(Mandatory = $true)][string]$StopFile,
    [int]$IntervalSec = 5,
    [int]$TimeoutSec = 60
)

$ErrorActionPreference = 'Continue'

function Get-ContainerBytes {
    $map = @{}
    try {
        $rows = docker stats --no-stream --format '{{.Name}}|{{.MemUsage}}' 2>$null
        if (-not $rows) { return $map }
        foreach ($row in $rows) {
            $parts = $row -split '\|', 2
            if ($parts.Count -ne 2) { continue }
            $usage = ($parts[1] -split '/')[0].Trim()
            if ($usage -match '([\d\.,]+)\s*([KMG]i?B)') {
                $value = [double]($matches[1] -replace ',', '.')
                $unit = $matches[2].ToUpperInvariant()
                $bytes = switch ($unit) {
                    'GIB' { $value * 1GB }
                    'MIB' { $value * 1MB }
                    'KIB' { $value * 1KB }
                    default { $value }
                }
                $map[$parts[0].Trim()] = [int64]$bytes
            }
        }
    } catch {
        # docker недоступен — сэмпл пойдёт без контейнеров, это не повод падать
    }
    return $map
}

function Get-GpuBytes {
    $result = $null
    try {
        $raw = nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader,nounits 2>$null
        if ($raw) {
            $first = ($raw -split "`n")[0] -split ','
            if ($first.Count -ge 2) {
                $result = @{
                    used_mb  = [int]($first[0].Trim())
                    total_mb = [int]($first[1].Trim())
                }
            }
        }
    } catch {
        # нет NVIDIA GPU — поле gpu останется null
    }
    return $result
}

function Get-HostBytes {
    $os = Get-CimInstance Win32_OperatingSystem
    return @{
        total_bytes     = [int64]$os.TotalVisibleMemorySize * 1KB
        free_bytes      = [int64]$os.FreePhysicalMemory * 1KB
        used_bytes      = [int64]($os.TotalVisibleMemorySize - $os.FreePhysicalMemory) * 1KB
        compress_bytes  = 0
    }
}

Set-Content -LiteralPath $Out -Value '' -Encoding UTF8
$deadline = (Get-Date).AddSeconds($TimeoutSec * 60)

while (-not (Test-Path -LiteralPath $StopFile)) {
    $hostInfo = Get-HostBytes
    $sample = [ordered]@{
        ts         = (Get-Date).ToUniversalTime().ToString('o')
        host       = $hostInfo
        gpu        = Get-GpuBytes
        containers = Get-ContainerBytes
    }
    Add-Content -LiteralPath $Out -Value ($sample | ConvertTo-Json -Depth 4 -Compress) -Encoding UTF8

    if ((Get-Date) -gt $deadline) {
        # Страховка от зависшего wrapper'а: сэмплируем час и выходим.
        break
    }
    Start-Sleep -Seconds $IntervalSec
}
