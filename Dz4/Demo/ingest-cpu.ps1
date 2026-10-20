<#
.SYNOPSIS
    Наполнение базы демо-стенда НА ПРОЦЕССОРЕ. Отдельный скрипт: стенду ингест не нужен.

.DESCRIPTION
    Стенд (`Demo/demo-cpu.ps1`, `Demo/demo.sh`) поднимает девять сервисов и НЕ поднимает
    ingestion-api: запросы идут и без него. Это разделение сделано в инфраструктуре
    (ADR-058) и оформлено здесь, в виде отдельной пары скриптов.

    Отличается от `ingest.ps1` одной вещью: LLM считает на процессоре. Нужен на машине без
    NVIDIA. Видеокарту выдаёт compose сам, поэтому базового файла достаточно: без оверлея
    `compose.runtime-gpu.yaml` бронь устройства не объявлена вовсе.

    Модель целиком считает на процессоре: это медленно, наполнение одного документа может
    идти часами. Если нужна скорость - используйте `ingest.ps1`, он запускает LLM на
    видеокарте. Матрица вариантов - ADR-065.

    Почему разделение не мешает: тома наборов общие. Ингест пишет в тот же Neo4j, который
    читает стенд, и после остановки оверлея база остаётся на месте. Стенду нужен
    наполненный том, а не сервис, который его наполняет.

    Наполнить базу можно двумя способами, и оба теперь лежат здесь:
      - загрузить документы этим скриптом (`ingest-cpu.ps1 load`);
      - взять готовый набор архивом (`demo-cpu.ps1 restore`).

.EXAMPLE
    powershell -File Demo\ingest-cpu.ps1 up -Set empty

.EXAMPLE
    powershell -File Demo\ingest-cpu.ps1 load -Path ..\Dz4\docs -Domain it
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('up', 'down', 'status', 'load', 'help')]
    [string]$Command = 'help',

    [ValidateSet('empty', 'ready')]
    [string]$Set = 'empty',

    [int]$WaitTimeoutSec = 300,
    [string]$Path,
    [string]$Domain = 'it'
)

$ErrorActionPreference = 'Stop'

# Те же значения, что и на стенде: тома наборов общие, и смена пароля здесь означала бы
# то же, что и на стенде, - том придётся пересоздавать вместе со всем наполнением.
$DEMO_PASSWORD = 'local-stand-not-a-secret'
$DEMO_API_KEY = 'demo-key-2026'

$scriptDir = $PSScriptRoot
$infraDir = Resolve-Path (Join-Path $scriptDir '..\prototype\infra')
$composeRuntime = Join-Path $infraDir 'compose.runtime.yaml'
$composeIngest = Join-Path $infraDir 'compose.ingest.yaml'
# Оверлея GPU здесь нет и он не подкладывается: бронь видеокарты выдаёт compose сам, и
# без неё модель считает на процессоре. Базового файла достаточно.

$neo4jContainer = 'ohw-runtime-neo4j'
$ingestContainer = 'ohw-runtime-ingestion'

function Write-Usage {
    @'
Наполнение базы демо-стенда НА ПРОЦЕССОРЕ. Стенд поднимается отдельно:
Demo/demo-cpu.ps1 (Windows) и Demo/demo.sh (Linux, macOS).

    ingest-cpu.ps1 up      -Set empty|ready
    ingest-cpu.ps1 load    -Path ФАЙЛ|ПАПКА [-Domain it]
    ingest-cpu.ps1 down    -Set empty|ready
    ingest-cpu.ps1 status  -Set empty|ready

`up` поднимает стенд ВМЕСТЕ с ingestion-api. Всё остальное - запросы, поиск, UI - живёт
на стенде и ингеста не требует.

`load` принимает и файл, и папку: в папке берутся `*.md` и `*.txt`, по алфавиту.

Наполнение занимает часы, а не минуты: на одном документе измерено около 33 с на чанк
(см. Demo/measured-load-time.md), а извлечение на процессоре медленнее ещё. Для быстрой
демонстрации набор `ready` выгружается архивом и восстанавливается без ингеста:
Demo/demo-cpu.ps1 export / restore.

Этот файл запускает LLM на процессоре. На машине с видеокартой есть отдельный скрипт
`Demo/ingest.ps1`: он считает на GPU и наполняет базу заметно быстрее.
'@ | Write-Host
}

function Invoke-Native {
    param([string]$Exe, [string[]]$Arguments, [switch]$AllowFailure)
    & $Exe @Arguments
    if (-not $AllowFailure -and $LASTEXITCODE -ne 0) {
        throw "$Exe вернул код $LASTEXITCODE"
    }
}

function Get-ComposeArguments {
    # Оверлей ингеста подключается ВСЕГДА: это и есть назначение скрипта. Без него
    # ingestion-api не существует и грузить нечем.
    $args = @('--project-directory', $infraDir, '--file', $composeRuntime)
    $args += @('--file', $composeIngest, '--profile', 'ingest')
    return $args
}

function Invoke-Compose {
    param([string[]]$Arguments)
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & docker compose @(Get-ComposeArguments) @Arguments
        $code = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
    if ($code -ne 0) {
        throw "docker compose вернул код $code"
    }
}

function Set-CommonEnv {
    $env:VOL = $Set
    $env:NEO4J_PASSWORD = $DEMO_PASSWORD
    $env:GRAPH_AUTH_API_KEY = $DEMO_API_KEY
    # Ноль слоёв на GPU. Без видеокарты значение 99 бессмысленно, но полагаться на то, что
    # llama.cpp сам перейдёт на процессор, нельзя: молчаливое решение за ресурс выглядело бы
    # как «тормозит», а не как «считает не там». Ноль говорит прямо.
    $env:LLM_NGPU_LAYERS = '0'
}

function Test-ContainerRunning {
    param([string]$Name)
    return [bool](docker ps --filter "name=^/$Name$" --format '{{.Names}}')
}

function Get-ContainerPassword {
    $auth = docker exec $neo4jContainer printenv NEO4J_AUTH 2>$null
    if ($LASTEXITCODE -eq 0 -and $auth) {
        return ($auth.Trim() -split '/')[-1]
    }
    return ''
}

function Invoke-Upload {
    param([string]$Source, [string]$Domain)

    $name = Split-Path -Leaf $Source
    Write-Host "Загружаю $Source как docs/$name"
    Invoke-Native -Exe 'docker' -Arguments @('cp', $Source, "$ingestContainer`:/tmp/demo_doc.md")

    # Запросы идут изнутри контейнера: его Python уже есть, и это снимает зависимость
    # от Python на хосте и от вложенных кавычек в PowerShell.
    $script = @'
import json
import sys
import time
import urllib.request

domain, name = sys.argv[1], sys.argv[2]
API = "demo-key-2026"
BASE = "http://localhost:8002"
SOURCE_URL = "docs/" + name


def call(method, url, body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json", "X-API-Key": API},
    )
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


text = open("/tmp/demo_doc.md", encoding="utf-8").read()

try:
    st = call("POST", f"{BASE}/api/v1/ingestion/documents/status",
              {"source_url": SOURCE_URL, "domain": domain,
               "doc_type": "md", "content": text})
    if st.get("unchanged"):
        print(f"  уже загружен в текущем виде (версия {st.get('version')}). Пропуск.")
        sys.exit(0)
    print(f"  загружен ранее: {st.get('known')}")
except Exception as exc:
    print(f"  спросить статус не удалось ({exc}). Продолжаю без проверки.")

job = call("POST", f"{BASE}/api/v1/ingestion/documents",
           {"source_url": SOURCE_URL, "domain": domain,
            "doc_type": "md", "content": text})
job_id = job["job_id"]
print(f"  джоба {job_id}, жду...")

final = None
for _ in range(3600):
    time.sleep(5)
    final = call("GET", f"{BASE}/api/v1/ingestion/jobs/{job_id}")
    if final.get("status") in ("succeeded", "failed", "cancelled"):
        break
else:
    raise SystemExit(f"  джоба {job_id} не завершилась за 3 часа")

print(f"  статус: {final['status']}")
if final.get("total_duration_ms"):
    print(f"  всего {final['total_duration_ms']} мс")
    print(f"  по стадиям {json.dumps(final.get('stage_durations_ms'), ensure_ascii=False)}")
if final["status"] != "succeeded":
    raise SystemExit(f"  джоба завершилась статусом {final['status']}")

# ПОТЕРЯ СЛОЯ LLM - ГЛАВНАЯ ПРОВЕРКА ЗДЕСЬ. Джоба завершается `succeeded` и при полной
# потере: конвейер уничтожает негодный LLM-слой и идёт дальше (ADR-032). Статус джобы
# этого не показывает, общее число узлей в базе - тоже (оно велико от других документов).
# Единственный канал, где потеря видна, - `enrichment` в ответе джобы.
enrichment = final.get("enrichment") or {}
lost_entities = int(enrichment.get("lost_entities") or 0)
lost_edges = int(enrichment.get("lost_edges") or 0)
cause = enrichment.get("cause")
if cause:
    declared = enrichment.get("llm_relations_declared")
    unique = enrichment.get("llm_relations_unique")
    lines = enrichment.get("llm_answer_lines")
    unique_lines = enrichment.get("llm_answer_lines_unique")
    print(f"  ДЕГРАДАЦИЯ: причина {cause}, потеряно сущностей {lost_entities}, связей {lost_edges}")
    print(f"    объявлено связей {declared}, уникальных {unique}"
          f" | строк ответа {lines}, уникальных {unique_lines}")
if lost_entities or lost_edges:
    raise SystemExit(
        f"  ПОТЕРЯ LLM-СЛОЯ: {lost_entities} сущностей и {lost_edges} связей уничтожены, "
        f"причина {cause}. Документ записан в базу БЕЗ этого слоя, а джоба числится "
        f"успешной. Повторная загрузка того же content_hash - no-op, экстракция не "
        f"перезапустится."
    )
print("  загрузка завершилась, потерь нет")
'@

    $script | docker exec -i $ingestContainer python - $Domain $name
    if ($LASTEXITCODE -eq 3) {
        throw 'загрузка прошла, но LLM-слой потерян. См. вывод выше.'
    }
    if ($LASTEXITCODE -ne 0) {
        throw "загрузка не удалась"
    }
}

function Assert-GraphGrew {
    # Проверка графа - НА ХОСТЕ. Внутри контейнера `docker` нет, и вызов оттуда падал бы
    # FileNotFoundError уже после успешной загрузки, то есть выглядел бы как сбой загрузки.
    Write-Host '  проверяю, что граф вырос'
    $pw = Get-ContainerPassword
    if (-not $pw) {
        Write-Warning '  количество нод НЕ измерено: не удалось получить пароль из контейнера'
        return
    }
    $out = docker exec $neo4jContainer cypher-shell -u neo4j -p $pw --format plain `
            'MATCH (n:ContextNode) RETURN count(*) AS c;'
    if ($LASTEXITCODE -ne 0) {
        Write-Warning '  количество нод НЕ измерено: запрос к базе не прошёл'
        return
    }
    $count = -join ($out | Select-Object -Skip 1 | ForEach-Object { $_ } | ForEach-Object {
            ($_ -replace '[^0-9]', '')
        })
    if (-not $count) {
        Write-Warning '  количество нод НЕ измерено: ответ не содержит числа'
        return
    }
    Write-Host "  ContextNode в базе: $count"
    if ([int]$count -eq 0) {
        throw 'граф пуст, а джоба succeeded - это vector-only загрузка'
    }
}

switch ($Command) {

    'up' {
        if (-not (Test-Path -LiteralPath $composeRuntime)) {
            throw "не найден $composeRuntime"
        }
        Set-CommonEnv
        Invoke-Compose @('up', '-d', '--wait', '--wait-timeout', $WaitTimeoutSec)
        Write-Host "База набора '$Set' поднята вместе с ingestion-api."
        Write-Host 'После наполнения опустите ингест: ingest-cpu.ps1 down'
        Write-Host 'Стенд поднимается отдельно: demo-cpu.ps1 up'
    }

    'load' {
        if (-not $Path) { throw 'нужен -Path ФАЙЛ или -Path ПАПКА' }
        if (-not (Test-ContainerRunning $neo4jContainer)) {
            throw 'база не поднята. Сначала: ingest-cpu.ps1 up'
        }
        if (-not (Test-ContainerRunning $ingestContainer)) {
            throw "ingestion-api не запущен. Нужен: ingest-cpu.ps1 up"
        }
        $resolved = (Resolve-Path -LiteralPath $Path).Path
        if (Test-Path -LiteralPath $resolved -PathType Container) {
            $files = @(Get-ChildItem -LiteralPath $resolved -File |
                    Where-Object { $_.Extension -in '.md', '.txt' } |
                    Sort-Object -Property Name)
            if ($files.Count -eq 0) {
                throw "в папке $resolved нет файлов *.md и *.txt"
            }
            Write-Host "В папке найдено файлов: $($files.Count). Порядок - по алфавиту."
            foreach ($file in $files) {
                Invoke-Upload -Source $file.FullName -Domain $Domain
                Assert-GraphGrew
                Write-Host ''
            }
            Write-Host "Загружено файлов: $($files.Count)"
        }
        else {
            Invoke-Upload -Source $resolved -Domain $Domain
            Assert-GraphGrew
            Write-Host 'Загружено.'
        }
    }

    'down' {
        Set-CommonEnv
        Invoke-Compose @('down')
        Write-Host "Набор '$Set' остановлен. Тома сохранены, наполнение осталось."
        Write-Host 'Запросы работают без ингеста: demo-cpu.ps1 up'
    }

    'status' {
        Write-Host '=== контейнеры ==='
        $running = @(docker ps --filter 'name=ohw-runtime' --format '{{.Names}}')
        if ($running.Count -eq 0) {
            Write-Host '  ни одного. Ни стенд, ни ингест не подняты.'
        }
        else {
            $running | ForEach-Object { Write-Host "  $_" }
        }
        $ingestUp = Test-ContainerRunning $ingestContainer
        if (-not $ingestUp) {
            Write-Host '  ingestion-api не запущен - грузить нечем, читать можно.'
        }

        Write-Host ''
        Write-Host "=== наполненность набора '$Set' ==="
        if (-not (Test-ContainerRunning $neo4jContainer)) {
            Write-Host '  Neo4j не отвечает. Это не «база пуста», это «базы нет».'
            break
        }
        $pw = Get-ContainerPassword
        if (-not $pw) {
            Write-Host '  не удалось получить пароль из контейнера - количество нод НЕ измерено.'
            break
        }
        $rows = @(docker exec $neo4jContainer cypher-shell -u neo4j -p $pw --format plain `
                'MATCH (n) RETURN labels(n)[0] AS label, count(*) AS c ORDER BY c DESC;' |
            Select-Object -Skip 1 | Where-Object { $_.Trim() -ne '' })
        if ($rows.Count -eq 0) {
            Write-Host '  база пуста: нод нет ни одного.'
        }
        else {
            $rows | ForEach-Object { Write-Host "  $_" }
            $ctx = [regex]::Match(($rows -join ' '), 'ContextNode,\s*(\d+)')
            if ($ctx.Success -and [int]$ctx.Groups[1].Value -eq 0) {
                Write-Warning 'Chunk есть, а ContextNode нет - это vector-only загрузка.'
                Write-Warning 'Джоба при этом завершается succeeded, то есть выглядит как успех.'
            }
        }
    }

    'help' { Write-Usage }
}
