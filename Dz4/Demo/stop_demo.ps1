<#
.SYNOPSIS
    Останавливает демонстрационный стенд.

.DESCRIPTION
    Тома НЕ удаляются: набор `ready` переживает перезапуск, и это его назначение.
    Удаление томов - отдельное осознанное действие, а не следствие `down`.
#>
[CmdletBinding()]
param(
    [ValidateSet('empty', 'ready')]
    [string]$Set = 'empty',
    [switch]$WithIngest
)

$ErrorActionPreference = 'Stop'

$demoRoot = $PSScriptRoot
$infra = Resolve-Path (Join-Path $demoRoot '..\prototype\infra')
$compose = Join-Path $infra 'compose.runtime.yaml'

if (-not (Test-Path -LiteralPath $compose)) {
    throw "не найден compose-файл стенда: $compose"
}

$env:VOL = $Set

$args = @('--file', $compose)
if ($WithIngest) {
    $args = @('--file', $compose, '--file', (Join-Path $infra 'compose.ingest.yaml'))
}

$previous = $ErrorActionPreference
try {
    $ErrorActionPreference = 'Continue'
    & docker compose @args --profile ingest down
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose вернул код $LASTEXITCODE"
    }
}
finally {
    $ErrorActionPreference = $previous
}

Write-Host "Demo: стенд '$Set' остановлен. Тома ohw-runtime_${Set}_* сохранены."
Write-Host 'Удалить их вместе с данными: docker volume rm ohw-runtime_<набор>_*'