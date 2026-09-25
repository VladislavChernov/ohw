<#
.SYNOPSIS
    Хостовый wrapper прогона eval-стенда: артефакты, логи сервисов, пик ресурсов.

.DESCRIPTION
    Раннер внутри контейнера не видит docker stats/nvidia-smi, поэтому всю
    внешнюю обвязку берёт на себя этот скрипт:

      1. проверяет/поднимает стенд (`up -d --wait`);
      2. запускает фоновый сборщик ресурсов (eval_sample_resources.ps1);
      3. выполняет `docker compose run --rm eval-runner run_eval.py ...`;
      4. останавливает сборщик и пишет resources.json (старт/пик/финал);
      5. выгружает логи сервисов в logs/*.log — иначе они умрут вместе с prune;
      6. дописывает строку прогона в reports/INDEX.md и reports/index.json.

    Каждый прогон получает свою папку `reports/<RunName>/` с паспортом
    (PASSPORT.md пишет раннер). Wrapper дописывает то, что видно только с хоста.

.PARAMETER RunName
    Имя папки прогона. По умолчанию `run-<yyyyMMdd-HHmmss>-<mode>`.

.PARAMETER Documents
    Явный список документов корпуса (пути относительно --corpus). Для усечённых
    прогонов; попадает в манифест как corpus_documents.

.PARAMETER LimitDocs
    Взять первые N документов корпуса (smoke-прогон).

.EXAMPLE
    .\run_eval_run.ps1 -RunName smoke-3docs -LimitDocs 3 -RetrievalOnly `
        -Note 'smoke: проверка стенда и RAM на 3 документах'

.EXAMPLE
    .\run_eval_run.ps1 -RunName smoke-3docs -Documents 01_ontology_and_domain_profile.md,data_model.md,invariants.md -RetrievalOnly
#>
[CmdletBinding()]
param(
    [string]$Project = 'ohw-eval-minimal',
    [string]$RunName,
    [ValidateSet('both', 'baseline', 'hybrid')]
    [string]$Mode = 'both',
    [string]$Domain = 'it',
    [string]$Corpus = '/repo/docs',
    [string[]]$Documents,
    [int]$LimitDocs,
    [string]$Note = '',
    [string[]]$Dataset = @('/app/infra/eval/it/questions.jsonl'),
    [string[]]$ExtraDataset = @('/app/infra/eval/it/questions_graph.jsonl'),
    [string]$SourcePrefix = 'docs',
    [switch]$NoJudge,
    [switch]$RetrievalOnly,
    [switch]$Trace,
    [string]$CompareWith,
    [int]$SampleIntervalSec = 5,
    [switch]$SkipUp,
    [switch]$KeepStack
)

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$infraDir = Split-Path -Parent $scriptDir
$repoRoot = Split-Path -Parent (Split-Path -Parent $infraDir)
$composeFile = Join-Path $infraDir 'compose.eval-minimal.yaml'
$reportsRoot = Join-Path (Split-Path -Parent $infraDir) 'reports'
$samplerScript = Join-Path $scriptDir 'eval_sample_resources.ps1'

if (-not $RunName) {
    $RunName = 'run-{0}-{1}' -f (Get-Date -Format 'yyyyMMdd-HHmmss'), $Mode
}
$runDir = Join-Path $reportsRoot $RunName
$samplesPath = Join-Path $runDir 'resources.samples.jsonl'
$stopFile = Join-Path $runDir 'resources.stop'
$logsDir = Join-Path $runDir 'logs'

function Get-GitTreeState {
    <#
      code_commit не воспроизводит состояние кода, если дерево грязное.
      Возвращает "clean" либо "dirty:<короткий хеш состава изменений>".
    #>
    try {
        Push-Location $repoRoot
        try {
            $porcelain = @(git status --porcelain 2>$null)
            if ($LASTEXITCODE -ne 0 -or $porcelain.Count -eq 0) { return 'clean' }
            $joined = ($porcelain | Sort-Object) -join "`n"
            $sha = [System.Security.Cryptography.SHA256]::Create()
            try {
                $bytes = [System.Text.Encoding]::UTF8.GetBytes($joined)
                $hash = [System.BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-', '').Substring(0, 12)
            } finally {
                $sha.Dispose()
            }
            return "dirty:$hash"
        } finally {
            Pop-Location
        }
    } catch {
        return 'unknown'
    }
}

function Invoke-Compose {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$ComposeArgs)
    & docker compose -p $Project -f $composeFile @ComposeArgs
}

# --- предусловия ----------------------------------------------------------

if (-not (Test-Path -LiteralPath $composeFile)) {
    throw "Не найден compose-файл: $composeFile"
}
if (-not $env:RUN_CODE_COMMIT) {
    $env:RUN_CODE_COMMIT = (& git -C $repoRoot rev-parse --short HEAD).Trim()
    Write-Host "RUN_CODE_COMMIT не задан — беру из HEAD: $($env:RUN_CODE_COMMIT)"
}
$env:RUN_CODE_TREE = Get-GitTreeState
if (-not $env:GRAPH_AUTH_API_KEY) { $env:GRAPH_AUTH_API_KEY = 'changeme' }
if (-not $env:NEO4J_PASSWORD) { $env:NEO4J_PASSWORD = 'graphrag' }

New-Item -ItemType Directory -Force -Path $runDir | Out-Null
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

# --- аргументы раннера ----------------------------------------------------

$evalArgs = @('/app/infra/eval/run_eval.py',
    '--domain', $Domain,
    '--mode', $Mode,
    '--corpus', $Corpus,
    '--out', "/reports/$RunName",
    '--source-prefix', $SourcePrefix)
foreach ($ds in $Dataset) { $evalArgs += @('--dataset', $ds) }
foreach ($ds in $ExtraDataset) { if ($ds) { $evalArgs += @('--extra-dataset', $ds) } }
foreach ($doc in ($Documents | Where-Object { $_ })) { $evalArgs += @('--documents', $doc) }
if ($PSBoundParameters.ContainsKey('LimitDocs')) { $evalArgs += @('--limit-docs', "$LimitDocs") }
if ($Note) { $evalArgs += @('--note', $Note) }
if ($NoJudge) { $evalArgs += '--no-judge' }
if ($RetrievalOnly) { $evalArgs += '--retrieval-only' }
if ($Trace) { $evalArgs += '--trace' }
if ($CompareWith) { $evalArgs += @('--compare-with', $CompareWith) }

$hostCommand = 'docker compose -p {0} -f {1} run --rm eval-runner python {2}' -f `
    $Project, $composeFile, ($evalArgs -join ' ')
Set-Content -LiteralPath (Join-Path $runDir 'command.host.txt') -Value $hostCommand -Encoding UTF8

# --- 1. стенд -------------------------------------------------------------

if (-not $SkipUp) {
    Write-Host "==> поднимаю стенд $Project"
    Invoke-Compose 'config' '--quiet'
    if ($LASTEXITCODE -ne 0) { throw 'Compose config невалиден' }
    Invoke-Compose 'up' '-d' '--wait' '--wait-timeout' '600' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Сервисы стенда не стали healthy' }
}

# --- 2. фоновый сборщик ресурсов ------------------------------------------

Write-Host "==> запускаю сборщик ресурсов (интервал ${SampleIntervalSec}s)"
if (Test-Path -LiteralPath $stopFile) { Remove-Item -LiteralPath $stopFile -Force }
$samplerArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $samplerScript,
    '-Project', $Project, '-IntervalSec', "$SampleIntervalSec",
    '-Out', $samplesPath, '-StopFile', $stopFile)
$sampler = Start-Process -FilePath 'powershell.exe' `
    -ArgumentList $samplerArgs -WindowStyle Hidden -PassThru

# --- 3. прогон ------------------------------------------------------------

$startedAt = Get-Date
Write-Host "==> прогон $RunName"
Invoke-Compose 'run' '--rm' '--no-deps' 'eval-runner' 'python' @($evalArgs[1..($evalArgs.Count - 1)])
$runExit = $LASTEXITCODE
$finishedAt = Get-Date

# --- 4. resources.json ----------------------------------------------------

if (Test-Path -LiteralPath $stopFile) { Remove-Item -LiteralPath $stopFile -Force }
Start-Sleep -Seconds 2
if ($sampler -and -not $sampler.HasExited) {
    try { $sampler.Kill() } catch { }
}

$resources = $null
if (Test-Path -LiteralPath $samplesPath) {
    $samples = @(Get-Content -LiteralPath $samplesPath -Encoding UTF8 |
        Where-Object { $_.Trim() } |
        ForEach-Object { try { $_ | ConvertFrom-Json } catch { $null } } |
        Where-Object { $_ })
    if ($samples.Count -gt 0) {
        $first = $samples[0]
        $last = $samples[-1]
        $peak = $samples |
            Sort-Object { [double]$_.host.used_bytes } -Descending |
            Select-Object -First 1
        $gpuPeak = $samples |
            Where-Object { $_.gpu } |
            Sort-Object { [int]$_.gpu.used_mb } -Descending |
            Select-Object -First 1
        $containerPeak = @{}
        foreach ($s in $samples) {
            foreach ($prop in $s.containers.PSObject.Properties) {
                $name = $prop.Name
                $value = [int64]$prop.Value
                if (-not $containerPeak.ContainsKey($name) -or $value -gt $containerPeak[$name]) {
                    $containerPeak[$name] = $value
                }
            }
        }
        # PS 5.1 не допускает `if` как выражение внутри литерала хеш-таблицы,
        # поэтому optional-поля считаем заранее.
        $gpuPeakMb = $null
        $gpuTotalMb = $null
        if ($gpuPeak) {
            $gpuPeakMb = $gpuPeak.gpu.used_mb
            $gpuTotalMb = $gpuPeak.gpu.total_mb
        }
        $resources = [ordered]@{
            samples              = $samples.Count
            interval_sec         = $SampleIntervalSec
            started_at           = $startedAt.ToUniversalTime().ToString('o')
            finished_at          = $finishedAt.ToUniversalTime().ToString('o')
            wall_time_s          = [int]([math]::Round(($finishedAt - $startedAt).TotalSeconds, 1))
            run_exit_code        = $runExit
            host_total_bytes     = $first.host.total_bytes
            host_used_start_gb   = [math]::Round([double]$first.host.used_bytes / 1GB, 2)
            host_used_peak_gb    = [math]::Round([double]$peak.host.used_bytes / 1GB, 2)
            host_used_final_gb   = [math]::Round([double]$last.host.used_bytes / 1GB, 2)
            host_free_peak_gb    = [math]::Round(1 - ([double]$peak.host.used_bytes / [double]$peak.host.total_bytes), 2)
            gpu_peak_used_mb     = $gpuPeakMb
            gpu_total_mb         = $gpuTotalMb
            container_peak_bytes = $containerPeak
        }
        $resources | ConvertTo-Json -Depth 5 |
            Set-Content -LiteralPath (Join-Path $runDir 'resources.json') -Encoding UTF8
    }
}

# --- 5. логи сервисов -----------------------------------------------------

Write-Host '==> выгружаю логи сервисов'
try {
    $services = @(& docker compose -p $Project -f $composeFile config --services 2>$null |
        Where-Object { $_ -and $_ -ne 'eval-runner' })
    foreach ($service in $services) {
        $target = Join-Path $logsDir "$service.log"
        & docker compose -p $Project -f $composeFile logs --no-color --no-log-prefix $service 2>&1 |
            Set-Content -LiteralPath $target -Encoding UTF8
    }
} catch {
    Write-Warning "Не удалось выгрузить логи сервисов: $($_.Exception.Message)"
}

# --- 6. индекс прогонов ---------------------------------------------------

function Update-RunIndex {
    $indexJson = Join-Path $reportsRoot 'index.json'
    $indexMd = Join-Path $reportsRoot 'INDEX.md'

    $resWall = $null
    $resRam = $null
    $resGpu = $null
    if ($resources) {
        $resWall = $resources.wall_time_s
        $resRam = $resources.host_used_peak_gb
        $resGpu = $resources.gpu_peak_used_mb
    }

    $entry = [ordered]@{
        run               = $RunName
        date              = $startedAt.ToString('yyyy-MM-dd HH:mm')
        commit            = $env:RUN_CODE_COMMIT
        tree              = $env:RUN_CODE_TREE
        mode              = $Mode
        documents         = $null
        limit_docs        = $LimitDocs
        verdict           = $null
        recall_baseline   = $null
        recall_target     = $null
        graph_axis_active = $null
        degraded_questions = $null
        wall_time_s       = $resWall
        host_peak_gb      = $resRam
        gpu_peak_mb       = $resGpu
        exit_code         = $runExit
    }

    $manifestPath = Join-Path $runDir 'run_manifest.json'
    if (Test-Path -LiteralPath $manifestPath) {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $entry.documents = $manifest.corpus_documents_count
        $entry.limit_docs = $manifest.corpus_limit
    }
    $reportPath = Join-Path $runDir 'lift_report.json'
    if (Test-Path -LiteralPath $reportPath) {
        $report = Get-Content -LiteralPath $reportPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $entry.verdict = $report.verdict
        $entry.recall_baseline = $report.baseline.retrieval.recall_at_k
        $entry.recall_target = $report.target.retrieval.recall_at_k
        $entry.graph_axis_active = $report.target.graph_axis_active
        $entry.degraded_questions = $report.target.graph_contribution.degraded_questions
    }

    $rows = @()
    if (Test-Path -LiteralPath $indexJson) {
        $rows = @(Get-Content -LiteralPath $indexJson -Raw -Encoding UTF8 | ConvertFrom-Json)
    }
    $rows = @($rows | Where-Object { $_.run -ne $RunName })
    $rows += [pscustomobject]$entry
    $rows | Sort-Object date | ConvertTo-Json -Depth 5 |
        Set-Content -LiteralPath $indexJson -Encoding UTF8

    $header = @(
        '# Прогоны eval',
        '',
        'Генерируется `run_eval_run.ps1`. Подробности условий — в `PASSPORT.md` папки прогона.',
        '',
        '| Прогон | Дата | Commit | Доков | Recall b/t | Вердикт | Граф | Degraded | Пик RAM | Пик VRAM | Время | Exit |',
        '|-------|------|--------|-------|------------|---------|------|----------|----------|----------|-------|------|')
    $lines = foreach ($row in ($rows | Sort-Object date)) {
        $graph = if ($null -eq $row.graph_axis_active) { 'n/a' } elseif ($row.graph_axis_active) { 'да' } else { 'НЕТ' }
        '| {0} | {1} | `{2}` | {3} | {4}/{5} | `{6}` | {7} | {8} | {9} ГБ | {10} МБ | {11} с | {12} |' -f `
            $row.run, $row.date, $row.commit, $row.documents,
            $row.recall_baseline, $row.recall_target, $row.verdict,
            $graph, $row.degraded_questions, $row.host_peak_gb, $row.gpu_peak_mb,
            $row.wall_time_s, $row.exit_code
    }
    Set-Content -LiteralPath $indexMd -Value (($header + $lines) -join "`n") -Encoding UTF8
}

try {
    Update-RunIndex
} catch {
    Write-Warning "Не удалось обновить индекс прогонов: $($_.Exception.Message)"
}

# --- 7. итог --------------------------------------------------------------

if (-not $KeepStack) {
    Write-Host '==> останавливаю стенд'
    Invoke-Compose 'stop' | Out-Null
}

Write-Host ''
Write-Host "Прогон: $RunName"
Write-Host "Папка:  $runDir"
if ($resources) {
    Write-Host ("Пик RAM: {0} ГБ | VRAM: {1} МБ | Время: {2} с" -f `
            $resources.host_used_peak_gb, $resources.gpu_peak_used_mb, $resources.wall_time_s)
}
Write-Host "Exit:   $runExit"
exit $runExit
