<#
.SYNOPSIS
    Поднимает демонстрационный стенд: пустой набор или набор с документами.

.DESCRIPTION
    Оба набора - один и тот же compose-файл, различается только значение VOL.
    Загрузки в runtime-контуре нет (ADR-058), поэтому набор `ready` для показа
    наполняется отдельно, скриптом load_document.ps1.

.PARAMETER Set
    `empty` - система без документов. `ready` - система с документами; том должен быть
    уже наполнен, иначе стенд поднимется пустым при имени `ready`.

.EXAMPLE
    powershell -File Demo\run_demo.ps1 -Set empty

.EXAMPLE
    powershell -File Demo\run_demo.ps1 -Set ready
#>
[CmdletBinding()]
param(
    [ValidateSet('empty', 'ready')]
    [string]$Set = 'empty',
    [int]$WaitTimeoutSec = 300
)

$ErrorActionPreference = 'Stop'

$demoRoot = $PSScriptRoot
$infra = Resolve-Path (Join-Path $demoRoot '..\prototype\infra')
$compose = Join-Path $infra 'compose.runtime.yaml'

if (-not (Test-Path -LiteralPath $compose)) {
    throw "не найден compose-файл стенда: $compose"
}

# Ключи и пароль НЕ генерируются на каждый запуск. Том переживает перезапуск стенда, а
# Neo4j применяет `NEO4J_AUTH` только при ПЕРВОМ старте базы: новый пароль на уже
# инициализированной базе молча игнорируется, и стенд падает как `unhealthy` с
# «authentication failure» в логе. Проверено на живом томе - сгенерированный пароль
# оказался неприменим, и базу пришлось переинициализировать вручную.
#
# Если ключ не задан, подставляется дефолт compose. Он виден, воспроизводим и работает на
# повторных запусках. Для стенда наружу пароль задаёт оператор окружения.
$env:GRAPH_AUTH_API_KEY = if ($env:GRAPH_AUTH_API_KEY) { $env:GRAPH_AUTH_API_KEY } else { 'demo-key' }
$env:NEO4J_PASSWORD = if ($env:NEO4J_PASSWORD) { $env:NEO4J_PASSWORD } else { 'demo-pass' }
$env:VOL = $Set

Write-Host "Demo: набор '$Set', пароль из окружения: $([bool]$env:NEO4J_PASSWORD)"

Write-Host "Demo: поднимаю набор '$Set' (тома ohw-runtime_${Set}_*)"

# docker compose пишет часть сообщений в stderr даже при успехе (`Container ... Running`),
# поэтому под `$ErrorActionPreference = 'Stop'` нативная ошибка становится terminating.
# Здесь это ожидаемый шум, и общий приём разбора выхода одинаков во всех скриптах Demo.
$previous = $ErrorActionPreference
try {
    $ErrorActionPreference = 'Continue'
    & docker compose --file $compose up -d --wait --wait-timeout $WaitTimeoutSec
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose вернул код $LASTEXITCODE"
    }
}
finally {
    $ErrorActionPreference = $previous
}

Write-Host ''
Write-Host "Demo: набор '$Set' поднят."
if ($Set -eq 'ready') {
    $empty = & docker volume ls --format '{{.Name}}' 2>$null |
        Where-Object { $_ -like "ohw-runtime_ready_*" }
    if (-not $empty) {
        Write-Warning 'Тома набора ready нет - стенд поднялся, но база будет пустой.'
        Write-Warning 'Наполнить: powershell -File Demo\load_document.ps1 -Path .\docs\<файл>.md -Domain it'
    }
}
Write-Host 'Остановить: powershell -File Demo\stop_demo.ps1'
Write-Host 'Состояние: powershell -File Demo\status_demo.ps1 -Set ready'