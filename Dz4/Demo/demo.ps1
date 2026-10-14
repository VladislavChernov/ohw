<#
.SYNOPSIS
    Демонстрационный стенд GraphRAG для Windows без bash.

.DESCRIPTION
    Тот же сценарий и те же команды, что в Demo/demo.sh, для Windows, где bash может
    отсутствовать. На Linux и macOS используйте demo.sh.

    Учётные данные заданы здесь и больше нигде. Пароль базы менять нельзя: том набора
    `ready` создан один раз, Neo4j применяет NEO4J_AUTH только при первом старте, и смена
    пароля означала бы пересоздание тома вместе со всем наполнением.

.EXAMPLE
    powershell -File Demo\demo.ps1 up --set empty --with-ingest

.EXAMPLE
    powershell -File Demo\demo.ps1 status --set ready
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('up', 'down', 'status', 'load', 'export', 'restore', 'help')]
    [string]$Command = 'help',

    [ValidateSet('empty', 'ready')]
    [string]$Set = 'empty',

    [switch]$WithIngest,
    [int]$WaitTimeoutSec = 300,
    [string]$Path,
    [string]$Domain = 'it',
    [string]$Out,
    [string]$Archive
)

$ErrorActionPreference = 'Stop'

$DEMO_PASSWORD = 'local-stand-not-a-secret'
$DEMO_API_KEY = 'demo-key-2026'

$scriptDir = $PSScriptRoot
$infraDir = Resolve-Path (Join-Path $scriptDir '..\prototype\infra')
$composeRuntime = Join-Path $infraDir 'compose.runtime.yaml'
$composeIngest = Join-Path $infraDir 'compose.ingest.yaml'
$fixturesDir = Join-Path $scriptDir 'fixtures'

$neo4jContainer = 'ohw-runtime-neo4j'

function Write-Usage {
    @'
Демо-стенд GraphRAG.

    demo.ps1 up       -Set empty|ready [-WithIngest]
    demo.ps1 down     -Set empty|ready [-WithIngest]
    demo.ps1 status   -Set empty|ready
    demo.ps1 load     -Path ФАЙЛ [-Domain it]
    demo.ps1 restore  -Set empty|ready [-Archive ФАЙЛ]
    demo.ps1 export   -Set ready [-Out ФАЙЛ]

`-WithIngest` дополнительно поднимает ingestion-api - он нужен, чтобы грузить документы в
пустую базу. Без него работает только просмотр того, что уже налито.

Наборы:
  empty - пустая система, тома создаются заново.
  ready - набор с документами. Тома в репозитории не хранятся; чтобы получить его на
          чистой машине, сначала выполните `restore`.
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
    $args = @('--project-directory', $infraDir, '--file', $composeRuntime)
    if ($WithIngest) {
        $args += @('--file', $composeIngest, '--profile', 'ingest')
    }
    return $args
}

function Invoke-Compose {
    param([string[]]$Arguments)
    # compose пишет часть сообщений в stderr даже при успехе, поэтому под
    # `$ErrorActionPreference = 'Stop'` нативная ошибка становится terminating.
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
}

function Test-Volume {
    param([string]$Name)
    return [bool](docker volume ls --format '{{.Name}}' | Where-Object { $_ -eq $Name })
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
    Invoke-Native -Exe 'docker' -Arguments @('cp', $Source, 'ohw-runtime-ingestion:/tmp/demo_doc.md')

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
print("  загрузка завершилась")
'@

    $script | docker exec -i ohw-runtime-ingestion python - $Domain $name
    if ($LASTEXITCODE -ne 0) {
        throw "загрузка не удалась"
    }

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
        if ($Set -eq 'ready' -and -not (Test-Volume 'ohw-runtime_ready_neo4j')) {
            Write-Warning 'том набора ready не создан. Поднимется ПУСТАЯ база с именем ready.'
            Write-Warning 'Чтобы получить наполненный набор: demo.ps1 restore'
        }
        Invoke-Compose @('up', '-d', '--wait', '--wait-timeout', $WaitTimeoutSec)
        Write-Host "Набор '$Set' поднят."
        Write-Host 'UI: http://localhost:8503'
    }

    'down' {
        Set-CommonEnv
        # Оверлей ингеста подключается ВСЕГДА, а не по флагу: если стенд поднимали с
        # -WithIngest, то без него ingestion-api остался бы работать после остановки.
        # Лишний оверлей при остановке безобиден.
        $WithIngest = $true
        Invoke-Compose @('down')
        Write-Host "Набор '$Set' остановлен. Тома сохранены."
    }

    'status' {
        Write-Host '=== контейнеры ==='
        $running = @(docker ps --filter 'name=ohw-runtime' --format '{{.Names}}')
        if ($running.Count -eq 0) {
            Write-Host '  ни одного. Стенд не поднят.'
        }
        else {
            $running | ForEach-Object { Write-Host "  $_" }
        }

        Write-Host ''
        Write-Host "=== тома набора '$Set' ==="
        $vols = @(docker volume ls --format '{{.Name}}' | Where-Object { $_ -like "ohw-runtime_${Set}_*" })
        if ($vols.Count -eq 0) {
            Write-Host '  тома нет. Набор ещё не поднимался.'
        }
        else {
            $vols | Sort-Object | ForEach-Object { Write-Host "  $_" }
        }

        Write-Host ''
        Write-Host '=== наполненность ==='
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

    'load' {
        if (-not $Path) { throw 'нужен -Path ФАЙЛ' }
        $resolved = (Resolve-Path -LiteralPath $Path).Path
        if (-not (Test-ContainerRunning $neo4jContainer)) {
            throw 'стенд не поднят. Сначала: demo.ps1 up -WithIngest'
        }
        if (-not (Test-ContainerRunning 'ohw-runtime-ingestion')) {
            throw 'ingestion-api не запущен. Нужен: demo.ps1 up -WithIngest'
        }
        Invoke-Upload -Source $resolved -Domain $Domain
        Write-Host 'Загружено.'
    }

    'export' {
        $vol = "ohw-runtime_${Set}_neo4j"
        if (-not (Test-Volume $vol)) { throw "том $vol не найден: набор '$Set' ещё не поднимался" }
        $target = if ($Out) { $Out } else { Join-Path $fixturesDir 'ready-neo4j.tar.gz' }
        $targetFull = [System.IO.Path]::GetFullPath($target)
        New-Item -ItemType Directory -Force -Path ([System.IO.Path]::GetDirectoryName($targetFull)) | Out-Null

        Write-Host "Архивирую $vol -> $targetFull (база будет остановлена на время выгрузки)"
        $wasUp = Test-ContainerRunning $neo4jContainer
        if ($wasUp) { Invoke-Native -Exe 'docker' -Arguments @('stop', $neo4jContainer) }
        try {
            Invoke-Native -Exe 'docker' -Arguments @(
                'run', '--rm',
                '--mount', "type=volume,source=$vol,target=/from",
                '--mount', "type=bind,source=$([System.IO.Path]::GetDirectoryName($targetFull)),target=/to",
                'alpine', 'sh', '-c', "tar czf /to/$([System.IO.Path]::GetFileName($targetFull)) -C /from ."
            )
        }
        finally {
            if ($wasUp) { Invoke-Native -Exe 'docker' -Arguments @('start', $neo4jContainer) -AllowFailure }
        }
        if (-not (Test-Path -LiteralPath $targetFull)) { throw "архив не появился: $targetFull" }
        $size = [math]::Round((Get-Item -LiteralPath $targetFull).Length / 1MB, 1)
        Write-Host "Готово: $targetFull ($size МБ)"
    }

    'restore' {
        # Набор - это ДВА тома: база с графом и реестр источников. Без реестра сервис не
        # знает, что документы уже загружены, и повторная загрузка того же документа
        # создаёт вторую копию вместо сигнала no-op.
        $plan = @(
            @{ Suffix = 'neo4j'; Archive = (Join-Path $fixturesDir 'ready-neo4j.tar.gz') },
            @{ Suffix = 'ingestion'; Archive = (Join-Path $fixturesDir 'ready-ingestion.tar.gz') }
        )
        foreach ($item in $plan) {
            $vol = "ohw-runtime_${Set}_$($item.Suffix)"
            $archiveFull = [System.IO.Path]::GetFullPath($item.Archive)

            if (-not (Test-Path -LiteralPath $archiveFull)) { throw "архив не найден: $archiveFull" }
            if (Test-Volume $vol) {
                Write-Warning "том $vol уже существует. Он будет ОЧИЩЕН."
                $answer = Read-Host '  Продолжить? (yes/no)'
                if ($answer -ne 'yes') { throw 'отменено' }
                Invoke-Native -Exe 'docker' -Arguments @('rm', '-f', $vol) -AllowFailure
            }
            Invoke-Native -Exe 'docker' -Arguments @('volume', 'create', $vol)
            Invoke-Native -Exe 'docker' -Arguments @(
                'run', '--rm',
                '--mount', "type=volume,source=$vol,target=/data",
                '--mount', "type=bind,source=$([System.IO.Path]::GetDirectoryName($archiveFull)),target=/to",
                'alpine', 'sh', '-c', "tar xzf /to/$([System.IO.Path]::GetFileName($archiveFull)) -C /data"
            )
            Write-Host "  восстановлен $vol"
        }
        Write-Host "Набор '$Set' восстановлен."
        Write-Host "Пароль базы: $DEMO_PASSWORD   ключ API: $DEMO_API_KEY"
    }

    'help' { Write-Usage }
}