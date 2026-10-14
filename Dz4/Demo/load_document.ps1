<#
.SYNOPSIS
    Кладёт документ в базу: отправляет в API и дождается терминального состояния джобы.

.DESCRIPTION
    Кандидаты производит сам ingestion-пайплайн, перед `EXTRACT` и после проверки no-op
    (ADR-053, решение владельца от 2026-10-14). Ничего готовить не нужно: отдельной
    стадии, папки `pre-ingest/` и переменной `CANDIDATES_DIR` больше нет.

    Порядок:
      1. спросить у ingestion-api, не загружен ли документ в текущем виде
         (`POST /api/v1/ingestion/documents/status`). Ответ приходит от сервиса и
         считает `content_hash` его же ридером, поэтому расхождение исключено;
      2. если документ новый или изменился - отправить его в API и дождаться конца;
      3. проверить, что граф вырос. Джоба `succeeded` сама по себе не доказательство:
         vector-only загрузка тоже завершается `succeeded`.

    Файлы джобы - документ и его кандидаты - убираются сервисом после успеха. Для
    отладки поставьте `CANDIDATES_KEEP=1` ДО поднятия стенда.

.PARAMETER Path
    Документ: .md или .txt.

.PARAMETER Domain
    Домен профиля, обычно `it`.

.EXAMPLE
    powershell -File Demo\load_document.ps1 -Path .\Demo\incoming\04_services_config.md -Domain it
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Path,
    [string]$Domain = 'it',
    [string]$IngestionUrl = 'http://localhost:8002',
    [string]$Neo4jPassword = $env:NEO4J_PASSWORD,
    [switch]$SkipStatusCheck,
    [int]$WaitTimeoutSec = 1800
)

$ErrorActionPreference = 'Stop'

$resolved = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path
if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
    throw "документ не найден: $resolved"
}

$extension = [System.IO.Path]::GetExtension($resolved).TrimStart('.').ToLower()
if ($extension -notin @('md', 'txt')) {
    throw "документ .$extension не поддерживается: ридеры знают md, txt и pdf"
}

$content = Get-Content -LiteralPath $resolved -Raw -Encoding UTF8
$sourceUrl = "docs/$([System.IO.Path]::GetFileName($resolved))"

function Invoke-Json {
    param([string]$Method, [string]$Uri, $Body)
    $params = @{
        Method      = $Method
        Uri         = $Uri
        ContentType = 'application/json; charset=utf-8'
        TimeoutSec  = 120
    }
    if ($null -ne $Body) { $params['Body'] = ($Body | ConvertTo-Json -Depth 6 -Compress) }
    Invoke-RestMethod @params
}

# 1. Спросить, не загружен ли документ. Отказ здесь не повод идти дальше молча: проход
#    мимо проверки выглядел бы как «проверка работает».
if (-not $SkipStatusCheck) {
    try {
        $status = Invoke-Json -Method Post `
            -Uri "$IngestionUrl/api/v1/ingestion/documents/status" `
            -Body @{
                source_url = $sourceUrl
                domain     = $Domain
                doc_type   = $extension
                content    = $content
            }
        if ($status.unchanged) {
            Write-Host "Demo: '$sourceUrl' уже загружен в текущем виде (версия $($status.version))."
            Write-Host 'Загрузка не выполнялась. Чтобы перезагрузить, измените документ или удалите источник.'
            return
        }
        Write-Host "Demo: '$sourceUrl' требует загрузки (известен: $($status.known))."
    }
    catch {
        Write-Warning "Demo: спросить статус не удалось ($($_.Exception.Message))."
        Write-Warning 'Продолжаю без проверки - на неизменившемся документе стадия отработает впустую.'
    }
}

# 2. Загрузка. Стадию кандидатов запускать не нужно: её вызывает пайплайн.
$job = Invoke-Json -Method Post `
    -Uri "$IngestionUrl/api/v1/ingestion/documents" `
    -Body @{
        source_url = $sourceUrl
        domain     = $Domain
        doc_type   = $extension
        content    = $content
    }
$jobId = $job.job_id
Write-Host "Demo: джоба $jobId, жду завершения..."

$deadline = (Get-Date).AddSeconds($WaitTimeoutSec)
$final = $null
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 5
    $final = Invoke-Json -Method Get -Uri "$IngestionUrl/api/v1/ingestion/jobs/$jobId"
    if ($final.status -in @('succeeded', 'failed', 'cancelled')) { break }
}
if ($null -eq $final -or $final.status -notin @('succeeded', 'failed', 'cancelled')) {
    throw "джоба $jobId не завершилась за $WaitTimeoutSec с (последний статус: $($final.status))"
}
Write-Host "Demo: статус $($final.status)"

if ($final.total_duration_ms) {
    $slowest = ($final.stage_durations_ms.PSObject.Properties |
        Sort-Object Value -Descending | Select-Object -First 1)
    Write-Host ("Demo: всего {0} мс, самая долгая стадия {1}={2} мс" -f
        $final.total_duration_ms, $slowest.Name, $slowest.Value)
    Write-Host "Demo: по стадиям: $($final.stage_durations_ms | ConvertTo-Json -Compress)"
}

if ($final.status -ne 'succeeded') {
    throw "джоба $jobId завершилась статусом $($final.status)"
}

# 3. Проверка наполнения. Джоба succeeded - не доказательство графа.
if (-not $Neo4jPassword) {
    Write-Warning 'Demo: NEO4J_PASSWORD не задан - количество узлов не проверено.'
    Write-Warning 'Задайте его или запустите: powershell -File Demo\status_demo.ps1'
}
else {
    $nodes = docker exec ohw-runtime-neo4j cypher-shell -u neo4j -p $Neo4jPassword `
        --format plain "MATCH (n:ContextNode) RETURN count(*) AS c;"
    $count = [int]([regex]::Match(($nodes -join ''), '\d+').Value)
    Write-Host "Demo: ContextNode в базе: $count"
    if ($count -eq 0) {
        throw 'Граф пуст, а джоба завершилась succeeded - это vector-only загрузка (ADR-053).'
    }
}

Write-Host "Demo: '$sourceUrl' загружен."
Write-Host 'Состояние: powershell -File Demo\status_demo.ps1'