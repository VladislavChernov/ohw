<#
.SYNOPSIS
    Демонстрационный стенд GraphRAG для Windows без bash.

.DESCRIPTION
    Сценарий демо-стенда для Windows, где bash может отсутствовать. На Linux и macOS
    используйте demo.sh.

    ИНГЕСТА ЗДЕСЬ НЕТ, и это не ограничение, а решение (ADR-065): стенду нужны запросы,
    поиск и UI, а наполнением базы занимается отдельный скрипт `Demo/ingest.ps1`
    (на Linux и macOS - `Demo/ingest.sh`). Наполненный том - общий, поэтому после
    `ingest.ps1 down` база остаётся на месте и стенд её читает.

    Учётные данные заданы здесь и больше нигде. Пароль базы менять нельзя: том набора
    `ready` создан один раз, Neo4j применяет NEO4J_AUTH только при первом старте, и смена
    пароля означала бы пересоздание тома вместе со всем наполнением.

.EXAMPLE
    powershell -File Demo\demo.ps1 up -Set ready

.EXAMPLE
    powershell -File Demo\demo.ps1 status -Set ready
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('up', 'down', 'status', 'export', 'restore', 'help')]
    [string]$Command = 'help',

    [ValidateSet('empty', 'ready')]
    [string]$Set = 'empty',

    # Отдельный запуск С реранкером bge. По умолчанию выключен: без этого флага слот
    # `RERANKER` остаётся `noop` и сервис реранкера не поднимается. Оверлей выдаёт
    # устройство, поэтому флагом в командной строке, как и видеокарту.
    [switch]$WithReranker,
    [int]$WaitTimeoutSec = 300,
    [string]$Out,
    [string]$Archive
)

$ErrorActionPreference = 'Stop'

$DEMO_PASSWORD = 'local-stand-not-a-secret'
$DEMO_API_KEY = 'demo-key-2026'

$scriptDir = $PSScriptRoot
$infraDir = Resolve-Path (Join-Path $scriptDir '..\prototype\infra')
$composeRuntime = Join-Path $infraDir 'compose.runtime.yaml'
$composeReranker = Join-Path $infraDir 'compose.runtime-reranker.yaml'
# Подкладывается ВСЕГДА: именно этот файл выдаёт контейнерам видеокарту. Без него LLM
# считает на процессоре - это не ошибка, а вариант `demo-cpu.ps1`.
$composeGpu = Join-Path $infraDir 'compose.runtime-gpu.yaml'
$fixturesDir = Join-Path $scriptDir 'fixtures'

$neo4jContainer = 'ohw-runtime-neo4j'

function Write-Usage {
    @'
Демо-стенд GraphRAG. ИНГЕСТА ЗДЕСЬ НЕТ: он в отдельном скрипте.

    demo.ps1 up       -Set empty|ready
    demo.ps1 down     -Set empty|ready
    demo.ps1 status   -Set empty|ready
    demo.ps1 restore  -Set empty|ready [-Archive ФАЙЛ]
    demo.ps1 export   -Set ready [-Out ФАЙЛ]

Наполнение базы - отдельными скриптами: Demo/ingest.ps1 (Windows), Demo/ingest.sh (Linux, macOS).
Тома наборов общие, поэтому наполненная база остаётся после `ingest.ps1 down`, а готовый
набор выгружается архивом и восстанавливается без ингеста: demo.ps1 export / restore.

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
    # Порядок важен: оверлей GPU идёт сразу за базовым, иначе его `deploy` перепишется.
    $args = @('--project-directory', $infraDir, '--file', $composeRuntime, '--file', $composeGpu)
    # Оверлея ингеста здесь нет и быть не должно: ingestion-api не входит в стенд
    # (ADR-058), и наполнением занимается отдельный скрипт (ADR-065).
    # Ранкерный оверлей идёт ПОСЛЕ оверлея GPU: он переопределяет слот `RERANKER` у
    # `query-api` и `query-worker`, и порядок файлов фиксирует, чьё значение победит.
    if ($WithReranker) {
        $args += @('--file', $composeReranker)
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


    'export' {
        # Тома набора. `query` намеренно не входит: в нём только журнал задач запросов, он
        # пуст, и compose создаст его при первом же запросе. Том `projection` входит: в нём
        # состояние инкрементальной проекции, без него набор не знает, что уже спроецировано.
        $suffixes = @('neo4j', 'ingestion', 'projection')
        foreach ($s in $suffixes) {
            $v = "ohw-runtime_${Set}_${s}"
            if (-not (Test-Volume $v)) { throw "том $v не найден: набор '$Set' ещё не поднимался" }
        }

        $targetDir = if ($Out) {
            [System.IO.Path]::GetDirectoryName([System.IO.Path]::GetFullPath($Out))
        }
        else { $fixturesDir }
        New-Item -ItemType Directory -Force -Path $targetDir | Out-Null

        $wasUp = Test-ContainerRunning $neo4jContainer
        if ($wasUp) { Invoke-Native -Exe 'docker' -Arguments @('stop', $neo4jContainer) }
        try {
            foreach ($s in $suffixes) {
                $v = "ohw-runtime_${Set}_${s}"
                $name = "ready-$s.tar.gz"
                $targetFull = Join-Path $targetDir $name
                Invoke-Native -Exe 'docker' -Arguments @(
                    'run', '--rm',
                    '--mount', "type=volume,source=$v,target=/from",
                    '--mount', "type=bind,source=$targetDir,target=/to",
                    'alpine', 'sh', '-c', "tar czf /to/$name -C /from ."
                )
                if (-not (Test-Path -LiteralPath $targetFull)) { throw "архив не появился: $targetFull" }
                $size = [math]::Round((Get-Item -LiteralPath $targetFull).Length / 1MB, 1)
                Write-Host "  $v -> $name ($size МБ)"
            }
        }
        finally {
            if ($wasUp) { Invoke-Native -Exe 'docker' -Arguments @('start', $neo4jContainer) -AllowFailure }
        }
        Write-Host "Набор '$Set' выгружен."
    }

    'restore' {
        # Набор - это несколько томов. Реестр источников обязателен: без него сервис не
        # знает, что документы уже загружены, и повторная загрузка того же документа
        # создаёт вторую копию вместо no-op. Проекция - чтобы набор знал, что уже
        # спроецировано.
        $plan = @(
            @{ Suffix = 'neo4j' },
            @{ Suffix = 'ingestion' },
            @{ Suffix = 'projection' }
        )
        foreach ($item in $plan) {
            $vol = "ohw-runtime_${Set}_$($item.Suffix)"
            # `if` и `Join-Path` нельзя ставить аргументом вызова в PowerShell 5.1 -
            # только в присваивании. Поэтому промежуточная переменная.
            $archivePath = Join-Path $fixturesDir "ready-$($item.Suffix).tar.gz"
            $archiveFull = [System.IO.Path]::GetFullPath($archivePath)

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
