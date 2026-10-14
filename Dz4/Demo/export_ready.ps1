<#
.SYNOPSIS
    Выгружает набор наружу: том базы упаковывается в архив.

.DESCRIPTION
    Набор - это том Docker, а не каталог в репозитории, поэтому отдать его наружу
    можно только так. Копировать базу в репозиторий нельзя: второе место хранения
    разъедется с оригиналом, и расхождение обнаружится только при следующей загрузке.

    Альтернатива архиву - отдать получателю скрипты и заполнить базу заново через
    `run_demo.ps1` и `load_document.ps1`. Она надёжнее: архив требует совместимого
    Docker и той же версии образа, а прогон - только стенд.

.EXAMPLE
    powershell -File Demo\export_ready.ps1

.EXAMPLE
    powershell -File Demo\export_ready.ps1 -Out D:\transfer\ready-graph.tar.gz
#>
[CmdletBinding()]
param(
    [ValidateSet('empty', 'ready')]
    [string]$Set = 'ready',
    [string]$Out
)

$ErrorActionPreference = 'Stop'

if (-not $Out) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $Out = Join-Path $PSScriptRoot "graph-$Set-$stamp.tar.gz"
}

$neo4jVolume = "ohw-runtime_${Set}_neo4j"
if (-not (docker volume ls --format '{{.Name}}' | Where-Object { $_ -eq $neo4jVolume })) {
    throw "том $neo4jVolume не найден. Набор '$Set' ещё не поднимался."
}

$outFull = [System.IO.Path]::GetFullPath($Out)
$outDir = [System.IO.Path]::GetDirectoryName($outFull)
$outName = [System.IO.Path]::GetFileName($outFull)

Write-Host "Demo: упаковываю $neo4jVolume -> $outFull"
Write-Host 'База остановлена на время выгрузки: контейнер Neo4j будет погашен и поднят обратно.'

$previous = $ErrorActionPreference
try {
    $ErrorActionPreference = 'Continue'
    docker stop ohw-runtime-neo4j
    docker run --rm `
        --mount "type=volume,source=$neo4jVolume,target=/from" `
        --mount "type=bind,source=$outDir,target=/to" `
        alpine sh -c "tar czf /to/$outName -C /from ."
    if ($LASTEXITCODE -ne 0) {
        throw "упаковка вернула код $LASTEXITCODE"
    }
    docker start ohw-runtime-neo4j
}
finally {
    $ErrorActionPreference = $previous
}

if (Test-Path -LiteralPath $outFull) {
    $size = [math]::Round((Get-Item -LiteralPath $outFull).Length / 1MB, 2)
    Write-Host "Demo: готов архив $outFull ($size МБ)"
}
else {
    throw "архив не появился: $outFull"
}

Write-Host ''
Write-Host 'Получатель: создать том, распаковать в него архив, затем run_demo.ps1 -Set <набор>.'
Write-Host 'Либо проще: отдать скрипты и заполнить базу заново через load_document.ps1.'