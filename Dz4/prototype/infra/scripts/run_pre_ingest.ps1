# Скрипт запуска стадии pre-ingest (ADR-053).
#
# Контракт: отдаём папку с документами и целевую папку, куда складываются результаты.
#   run_pre_ingest.ps1 -Src <папка с .md> [-Dst <папка для результатов>]
#
# По умолчанию Src=docs, Dst=pre-ingest. Стадия одноразовая: контейнер поднимается,
# отрабатывает и уходит. Стенд не затрагивается.
#
# Файл записан с BOM намеренно: Windows PowerShell 5.1 читает .ps1 в кодировке консоли,
# а не UTF-8, и без BOM кириллица в строках разваливает парсер файла.

[CmdletBinding()]
param(
    # Пути по умолчанию вычисляются в теле скрипта, а не здесь: при запуске
    # `powershell -File` значение `$PSScriptRoot` в блоке param ещё пустое, и
    # Join-Path падает с невнятной ошибкой привязки.
    [string]$Src,
    [string]$Dst,
    [string]$ComposeFile
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
if (-not $Src) {
    $Src = Join-Path $repoRoot 'docs'
}
if (-not $Dst) {
    $Dst = Join-Path $repoRoot 'pre-ingest'
}
if (-not $ComposeFile) {
    $ComposeFile = Join-Path $repoRoot 'prototype\infra\compose.preingest.yaml'
}

# docker compose пишет в stderr даже при успехе (`Container ... Running`), поэтому при
# `$ErrorActionPreference = 'Stop'` нативная ошибка становится terminating. Здесь это
# ожидаемый шум, а не отказ, поэтому внутри снимаем предпочтение и проверяем
# `$LASTEXITCODE` плюс непустой stdout.
function Invoke-ComposeChecked {
    param([string[]]$ComposeArgs)
    $previous = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $stdoutFile = [System.IO.Path]::GetTempFileName()
        try {
            & docker compose --file $ComposeFile @ComposeArgs 2>&1 |
                Tee-Object -FilePath $stdoutFile
            if ($LASTEXITCODE -ne 0) {
                throw "docker compose вернул код $LASTEXITCODE"
            }
            $output = Get-Content -LiteralPath $stdoutFile -Raw -Encoding UTF8
            # Пустой stdout при коде 0 — это провал, а не успех: раннер вышел раньше,
            # чем что-то сделал, и отчёт об этом должен быть ошибкой.
            if ([string]::IsNullOrWhiteSpace($output)) {
                throw 'стадия не напечатала ничего: пустой вывод при коде 0'
            }
        }
        finally {
            Remove-Item -LiteralPath $stdoutFile -Force -ErrorAction SilentlyContinue
        }
    }
    finally {
        $ErrorActionPreference = $previous
    }
}

if (-not (Test-Path -LiteralPath $ComposeFile)) {
    throw "не найден compose-файл стадии: $ComposeFile"
}

$resolvedSrc = (Resolve-Path -LiteralPath $Src -ErrorAction Stop).Path
if (Test-Path -LiteralPath $Dst) {
    $resolvedDst = (Resolve-Path -LiteralPath $Dst -ErrorAction Stop).Path
}
else {
    $parent = Split-Path -Parent $Dst
    if (-not (Test-Path -LiteralPath $parent)) {
        throw "родительская папка для результата не найдена: $parent"
    }
    New-Item -ItemType Directory -Path $Dst -Force | Out-Null
    $resolvedDst = (Resolve-Path -LiteralPath $Dst).Path
}

$docs = @(Get-ChildItem -LiteralPath $resolvedSrc -Filter '*.md' -File)
if ($docs.Count -eq 0) {
    throw "в папке источника нет .md: $resolvedSrc"
}

Write-Host "pre-ingest: источник $resolvedSrc ($($docs.Count) документов)"
Write-Host "pre-ingest: результат $resolvedDst"

$env:PRE_INGEST_SRC = $resolvedSrc
$env:PRE_INGEST_DST = $resolvedDst
try {
    Invoke-ComposeChecked -ComposeArgs @('run', '--rm', 'pre-ingest')
}
finally {
    # Пути не должны пережить запуск: иначе следующий вызов в этой сессии молча
    # переиспользует прошлый путь, и кандидаты окажутся привязаны не к тому документу.
    Remove-Item Env:PRE_INGEST_SRC -ErrorAction SilentlyContinue
    Remove-Item Env:PRE_INGEST_DST -ErrorAction SilentlyContinue
}

$produced = @(Get-ChildItem -LiteralPath $resolvedDst -Filter '*_candidates.json' -File)
if ($produced.Count -eq 0) {
    throw "стадия отработала, но файлов кандидатов в $resolvedDst нет"
}
Write-Host "pre-ingest: готово, файлов кандидатов $($produced.Count)"