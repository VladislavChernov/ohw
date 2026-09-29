<#
.SYNOPSIS
    Прогон E2E-сценария уборки (docs/test_plan.md 7.2) на стенде ohw-eval-qwen3b.

.DESCRIPTION
    Хостовая часть исполнителя. Само исполнение идёт внутри сети стенда - порты наружу не
    публикуются, - поэтому здесь только то, что с хоста и видно: сброс данных, извлечение
    посевов из git, чтение окружения контейнера, вычисление имени артефакта и запуск
    одноразового контейнера с исполнителем.

    Сброс НЕ трогает models_data: в нём 6.2 ГБ моделей (GGUF + кэш bge-m3), и удаление
    томов вместе с данными увело бы их, а скачивание занимает часы. Данные - это neo4j,
    ingestion, projection, config.

    Имя артефакта - <YYYYMMDD-HHMMSS>-<short-sha>, разрешение в секундах: два прогона идут
    с интервалом в минуты, и на минутном разрешении рискуют получить одно имя. Если папка
    уже есть, прогон падает, а не сливается с прошлым.

.PARAMETER Reset
    Сбросить тома данных и поднять стенд заново. Без этого флага стенд поднимается как есть.

.PARAMETER KeepUp
    Не выключать стенд после прогона.

.EXAMPLE
    .\run_e2e_cleanup.ps1 -Reset
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
$corpusRel = ".eval-corpus-old"
$corpusDir = Join-Path $workspace $corpusRel

function Write-Step($m) { Write-Host "== $m" -ForegroundColor Cyan }
function Invoke-Compose { & docker compose @composeArgs @args }

#: Значение переменной окружения контейнера, "" если её нет. Раньше здесь стояла связка
#: `(Select-String ...).ToString() -replace ... .Trim()`, и она падала на ровно двух
#: случаях, оба из которых в прогоне реальны: переменной нет (`DATA_RETENTION_MODE` в
#: compose не задан) и совпадений больше одного (`.ToString()` на массиве даёт
#: "System.Object[]"). Отсутствие переменной - это факт о стенде, а не ошибка скрипта.
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

Write-Step "посевы из git"
# SHA блоба сначала, потом запись по нему: ref с кареткой через cmd /c разбирается молча
# (^ - escape-символ cmd), а файл получается не тем срезом без всякой ошибки.
$seeds = @(
    @{ Ref = "6e13a63^:Dz4/docs/01_ontology_and_domain_profile.md"; Rel = "docs/01_ontology_and_domain_profile.md" },
    @{ Ref = "6a3f8c3:Dz4/docs/plans/cosine-dedup.md";               Rel = "docs/plans/cosine-dedup.md" }
)
foreach ($seed in $seeds) {
    $sha = (git -C $workspace rev-parse $seed.Ref).Trim()
    $target = Join-Path $corpusDir $seed.Rel
    $parent = Split-Path -Parent $target
    if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
    cmd /c "git -C `"$workspace`" cat-file -p $sha > `"$target`""
    $actual = (git -C $workspace hash-object $target).Trim()
    if ($actual -ne $sha) { throw "посев не тот: ожидали $sha, получили $actual ($($seed.Rel))" }
    Write-Host "  $($seed.Rel): $sha срез подтверждён"
}

Write-Step "имя артефакта"
$label = "{0}-{1}" -f (Get-Date -Format "yyyyMMdd-HHmmss"), $ShortSha
$artifactsRel = "test_artifacts/e2e-cleanup/$label"
$artifactsHost = Join-Path $workspace $artifactsRel
if (Test-Path -LiteralPath $artifactsHost) { throw "каталог артефакта уже существует: $artifactsHost" }
Write-Host "  $artifactsRel"

Write-Step "исполнитель"
$authLine = Get-ContainerEnv "${Project}-neo4j-1" "NEO4J_AUTH"
if (-not $authLine) { throw "NEO4J_AUTH не найден в окружении neo4j" }
$creds = $authLine -split "/", 2
$extractLlm = Get-ContainerEnv $ingestionContainer "EXTRACT_LLM"
$retention = Get-ContainerEnv $ingestionContainer "DATA_RETENTION_MODE"
$llmModel = Get-ContainerEnv $ingestionContainer "LLM_MODEL"
$apiKey = Get-ContainerEnv $ingestionContainer "AUTH_API_KEY"
if (-not $apiKey) { $apiKey = Get-ContainerEnv $ingestionContainer "GRAPH_AUTH_API_KEY" }
if (-not $apiKey) { $apiKey = "changeme" }
Write-Host "  EXTRACT_LLM=$extractLlm DATA_RETENTION_MODE='$retention' LLM_MODEL=$llmModel"

$envArgs = @(
    "-e", "PYTHONUTF8=1",
    "-e", "INGESTION_URL=http://ingestion-api:8002",
    "-e", "API_KEY=$apiKey",
    "-e", "NEO4J_HTTP=http://neo4j:7474",
    "-e", "NEO4J_USER=$($creds[0])",
    "-e", "NEO4J_PASSWORD=$($creds[1])",
    "-e", "DOMAIN=$Domain",
    "-e", "RELOAD_SOURCE_URL=docs/plans/cosine-dedup.md",
    "-e", "SUPPORT_SOURCE_URL=docs/01_ontology_and_domain_profile.md",
    "-e", "CORPUS_DIR=/project/$corpusRel",
    "-e", "FRESH_DOC_PATH=/project/docs/plans/cosine-dedup.md",
    "-e", "ARTIFACTS_DIR=/project/$artifactsRel",
    "-e", "SQLITE_PATH=/stand/ingestion.sqlite",
    "-e", "COMPOSE_FILES=compose.eval-minimal.yaml+compose.eval-qwen3b.yaml",
    "-e", "PROJECT=$Project",
    "-e", "EXTRACT_LLM=$extractLlm",
    "-e", "DATA_RETENTION_MODE=$retention",
    "-e", "LLM_MODEL=$llmModel",
    "-e", "INGESTION_CONTAINER=$ingestionContainer"
)
$runArgs = @(
    "run", "--rm", "-i",
    "--network", "${Project}_default",
    # Каталог проекта (`Dz4`) монтируется как `/project`. Раньше он назывался `/repo` и
    # пути вида `/repo/Dz4/...` уезжали в `Dz4/Dz4/...`: переменная разрешалась в `Dz4`, а
    # префикс `Dz4/` в пути считался лишним. Имя монтирования выбрано так, чтобы `Dz4` в
    # пути не встречалось ни разу.
    "-v", "${workspace}:/project",
    "-v", "${Project}_ingestion_data:/stand:ro",
    "-w", "/project/prototype"
) + $envArgs + @("ohw/dz4-dev:0.1.0", "python", "infra/eval/run_e2e_cleanup.py")

& docker @runArgs
$code = $LASTEXITCODE

Write-Step "итог"
if (Test-Path -LiteralPath $artifactsHost) {
    Get-ChildItem -LiteralPath $artifactsHost | ForEach-Object { Write-Host "  $($_.Name)" }
    $verdictPath = Join-Path $artifactsHost "verdict.json"
    if (Test-Path -LiteralPath $verdictPath) {
        $verdict = Get-Content -LiteralPath $verdictPath -Raw -Encoding UTF8 | ConvertFrom-Json
        Write-Host "  run_valid=$($verdict.run_valid)"
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
