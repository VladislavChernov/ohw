<#
.SYNOPSIS
    Прогон E2E-сценария глубины обхода (docs/test_plan.md 7.3, ADR-036) на стенде ohw-eval-qwen3b.

.DESCRIPTION
    Хостовая часть исполнителя. Исполнение идёт внутри сети стенда - порты наружу не
    публикуются, - поэтому здесь только то, что с хоста и видно: сброс данных, чтение
    окружения контейнера, вычисление имени артефакта и запуск одноразового контейнера.

    Образ исполнителя - ohw/eval-service:prototype, ТОТ ЖЕ, что у ingestion-api и
    eval-runner. Раньше здесь стоял ohw/dz4-dev:0.1.0, и это была ошибка: у него в
    /app/src лежит код от 2026-09-18, где нет ни MAX_EXPANSION_DEPTH, ни параметра
    max_depth в run(). Сценарий глубины обязан исполнять текущий код, иначе он проверяет
    не то, что разворачивается на стенде. Проверено: в текущем образе
    MAX_EXPANSION_DEPTH = 6 и run(max_depth) существует.

    Монтирования и окружение повторяют сервис eval-runner, потому что сценарий собирает
    конвейер тем же factory: /config, /profiles, тома projection, NEO4J_*, EMBEDDER.

    Сценарий идёт in-process, а не через POST /query: сервиса query-api на eval-стенде нет
    (compose.eval-minimal.yaml: config, glossary, neo4j, embeddings, ingestion, llm,
    eval-runner). HTTP-приём max_depth покрыт юнит-тестами tests/test_query_api.py; на стенде
    проверяется обход.

    Сброс НЕ трогает models_data: в нём 6.2 ГБ моделей (GGUF + кэш bge-m3), удаление томов
    вместе с данными увело бы их, а скачивание занимает часы.

    Посевы не берутся из git: четыре документа сценария заданы в исполнителе. Они и есть
    фикстура, и их текст часть проверки - якорный вопрос обязан находить документ посева.

    Имя артефакта - <YYYYMMDD-HHMMSS>-<short-sha>, разрешение в секундах. Если папка уже
    есть, прогон падает, а не сливается с прошлым.

.PARAMETER Reset
    Сбросить тома данных и поднять стенд заново. Без этого флага стенд поднимается как есть.

.PARAMETER KeepUp
    Не выключать стенд после прогона.

.EXAMPLE
    .\run_e2e_depth.ps1 -Reset
#>
[CmdletBinding()]
param(
    [switch]$Reset,
    [switch]$KeepUp,
    [string]$Domain = "it",
    [string]$Project = "ohw-eval-qwen3b",
    [string]$ShortSha = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$infra = Resolve-Path (Join-Path $here "..")
$workspace = Resolve-Path (Join-Path $infra "..\..")
$composeBase = Join-Path $infra "compose.eval-minimal.yaml"
$composeOverride = Join-Path $infra "compose.eval-qwen3b.yaml"
$composeArgs = @("-f", $composeBase, "-f", $composeOverride, "-p", $Project)

#: Образ исполнителя. Не путать с ohw/dz4-dev: в нём код от 2026-09-18, см. .DESCRIPTION.
$runnerImage = "ohw/eval-service:prototype"

function Write-Step($m) { Write-Host "== $m" -ForegroundColor Cyan }
function Invoke-Compose { & docker compose @composeArgs @args }

#: Значение переменной окружения контейнера, "" если её нет. Отсутствие переменной - это
#: факт о стенде, а не ошибка скрипта.
function Get-ContainerEnv([string]$Container, [string]$Name) {
    $fmt = '{{range .Config.Env}}{{println .}}{{end}}'
    $prefix = "$Name="
    $line = docker inspect $Container --format $fmt | Where-Object { $_.StartsWith($prefix) } | Select-Object -First 1
    if (-not $line) { return "" }
    return $line.Substring($prefix.Length).Trim()
}

Write-Step "ветка и коммит"
if (-not $ShortSha) { $ShortSha = (git -C $workspace rev-parse --short HEAD).Trim() }
$head = (git -C $workspace rev-parse HEAD).Trim()
Write-Host "  HEAD=$($head.Substring(0,7))"
# compose требует RUN_CODE_COMMIT, и без него даже `down` не разбирается - то есть сброс
# молча превращался в "ничего не удалено", а тома оставались занятыми контейнерами.
$env:RUN_CODE_COMMIT = $head

if ($Reset) {
    Write-Step "стенд вниз"
    Invoke-Compose down | Out-Null
    $leftover = docker ps -a --filter "name=$Project" --format "{{.Names}}"
    if ($leftover) { throw "после down остались контейнеры: $leftover" }
    foreach ($v in @("neo4j_data", "ingestion_data", "projection_data", "config_data")) {
        $name = "${Project}_$v"
        if (docker volume ls --filter "name=^${name}$" -q) {
            docker volume rm $name | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "том $name не удалён: он ещё занят" }
            Write-Host "  удалён том $name"
        }
    }
    Write-Host "  models_data сохранён: в нём модели, удаление увело бы 6.2 ГБ"
}

Write-Step "стенд вверх"
Invoke-Compose up -d --wait | Out-Null
$ingestionContainer = "${Project}-ingestion-api-1"
$stateFmt = '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{end}}'
$ingestionState = (docker inspect $ingestionContainer --format $stateFmt).Trim()
Write-Host "  $ingestionContainer : $ingestionState"

Write-Step "имя артефакта"
$label = "{0}-{1}" -f (Get-Date -Format "yyyyMMdd-HHmmss"), $ShortSha
$artifactsRel = "test_artifacts/e2e-depth/$label"
$artifactsHost = Join-Path $workspace $artifactsRel
if (Test-Path -LiteralPath $artifactsHost) { throw "каталог артефакта уже существует: $artifactsHost" }
Write-Host "  $artifactsRel"

Write-Step "исполнитель"
$authLine = Get-ContainerEnv "${Project}-neo4j-1" "NEO4J_AUTH"
if (-not $authLine) { throw "NEO4J_AUTH не найден в окружении neo4j" }
$creds = $authLine -split "/", 2
$neo4jPassword = $creds[1]
$extractLlm = Get-ContainerEnv $ingestionContainer "EXTRACT_LLM"
$llmModel = Get-ContainerEnv $ingestionContainer "LLM_MODEL"
$apiKey = Get-ContainerEnv $ingestionContainer "AUTH_API_KEY"
if (-not $apiKey) { $apiKey = Get-ContainerEnv $ingestionContainer "GRAPH_AUTH_API_KEY" }
if (-not $apiKey) { $apiKey = "changeme" }
Write-Host "  образ=$runnerImage EXTRACT_LLM=$extractLlm LLM_MODEL=$llmModel"
if ($llmModel -notlike "*3b*") { Write-Warning "LLM_MODEL не похож на 3B: $llmModel" }

$envArgs = @(
    "-e", "PYTHONUTF8=1",
    # Исходник из рабочего дерева, а не запечённый в образе. Без этого исполнитель
    # импортирует /app/src, то есть код на момент сборки образа: правка в дереве не
    # попадает в прогон, и E2E проверяет не то, что отлаживается. Финальный
    # подтверждающий прогон делается на пересобранном образе - тогда /app/src и дерево
    # совпадают, и этот путь можно убрать.
    "-e", "PYTHONPATH=/project/prototype/src",
    "-e", "AUTH_API_KEY=$apiKey",
    "-e", "X_API_KEY=$apiKey",
    "-e", "CONFIG_URL=http://config-service:8001",
    "-e", "GLOSSARY_URL=http://glossary-service:8003",
    "-e", "DOMAIN_PROFILES_DIR=/profiles",
    "-e", "NAMESPACES_PATH=/config/namespaces.yaml",
    "-e", "GRAPH_STORE=neo4j",
    "-e", "VECTOR_STORE=neo4j",
    "-e", "NEO4J_URI=bolt://neo4j:7687",
    "-e", "NEO4J_USER=$($creds[0])",
    "-e", "NEO4J_PASSWORD=$neo4jPassword",
    "-e", "NEO4J_HTTP=http://neo4j:7474",
    "-e", "EMBEDDER=bge_m3_service",
    # Порт embeddings - 8004, а не 8001: 8001 у config-service. Взят из окружения
    # ingestion-api, а не угадан.
    "-e", "EMBEDDINGS_URL=http://embeddings-service:8004",
    "-e", "EMBEDDING_MODEL=bge-m3",
    "-e", "EMBEDDING_DIMENSIONS=1024",
    "-e", "EMBEDDINGS_TIMEOUT_S=300",
    "-e", "INGESTION_URL=http://ingestion-api:8002",
    "-e", "PROJECTION_STATE_DB_PATH=/var/lib/graphrag/projection/projection.db",
    "-e", "PROJECTION_CONFIG_FINGERPRINT=default",
    "-e", "LLM_ADAPTER=openai",
    "-e", "LLM_BASE_URL=http://llm:8080",
    "-e", "LLM_MODEL=$llmModel",
    "-e", "LLM_TEMPERATURE=0",
    "-e", "LLM_TIMEOUT_S=300",
    "-e", "DOMAIN=$Domain",
    # Каталог артефакта - внутри уже смонтированного /project, а не отдельным mount'ом:
    # отдельный путь `/artifacts` без монтирования означал бы, что исполнитель пишет
    # в эфемерную файловую систему контейнера и всё исчезает вместе с `--rm`. Так и вышло
    # на первом прогоне: вердикт был напечатан в stdout, а на диске не осталось ничего.
    "-e", "ARTIFACTS_DIR=/project/test_artifacts/e2e-depth/$label",
    "-e", "COMPOSE_FILES=compose.eval-minimal.yaml+compose.eval-qwen3b.yaml",
    "-e", "PROJECT=$Project",
    "-e", "EXTRACT_LLM=$extractLlm",
    "-e", "RUN_CODE_COMMIT=$head"
)
$runArgs = @(
    "run", "--rm", "-i",
    "--network", "${Project}_default",
    # Каталог `Dz4` монтируется как `/project`, и исполнитель лежит в /project/prototype.
    # Имя монтирования выбрано так, чтобы `Dz4` в путях не встречалось.
    "-v", "${workspace}:/project",
    "-v", "${workspace}/prototype/infra/eval:/app/infra/eval:ro",
    "-v", "${workspace}/prototype/infra/config:/config:ro",
    "-v", "${workspace}/prototype/domain_profiles:/profiles:ro",
    "-v", "${Project}_projection_data:/var/lib/graphrag/projection",
    "-w", "/app"
) + $envArgs + @($runnerImage, "python", "/app/infra/eval/run_e2e_depth.py")

& docker @runArgs
$code = $LASTEXITCODE

Write-Step "итог"
if (Test-Path -LiteralPath $artifactsHost) {
    Get-ChildItem -LiteralPath $artifactsHost | ForEach-Object { Write-Host "  $($_.Name)" }
    $verdictPath = Join-Path $artifactsHost "verdict.json"
    if (Test-Path -LiteralPath $verdictPath) {
        $verdict = Get-Content -LiteralPath $verdictPath -Raw -Encoding UTF8 | ConvertFrom-Json
        Write-Host "  run_valid=$($verdict.run_valid)"
        $verdict.checks.PSObject.Properties | ForEach-Object {
            Write-Host ("  {0}: {1} - {2}" -f $_.Name, $_.Value.verdict, $_.Value.reason)
        }
    }
} else {
    Write-Host "  артефакт НЕ создан - это провал прогона, а не пустой результат"
}

if (-not $KeepUp) {
    Write-Step "стенд вниз"
    Invoke-Compose down | Out-Null
    Write-Host "  модели в ${Project}_models_data сохранены"
}
exit $code
