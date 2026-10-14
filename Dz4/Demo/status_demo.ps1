<#
.SYNOPSIS
    Состояние демонстрационного стенда: контейнеры, тома, наполненность графа.

.DESCRIPTION
    Отвечает на вопрос «чем сейчас наполнен стенд» и, что важнее, различает три разных
    состояния: нод нет, база не отвечает, база пуста. Отсутствие данных и отсутствие
    дефекта - разные результаты, и молчание здесь означало бы, что стенд готов к показу.
#>
[CmdletBinding()]
param(
    [ValidateSet('empty', 'ready')]
    [string]$Set = 'empty',
    [string]$Neo4jPassword = $env:NEO4J_PASSWORD
)

$ErrorActionPreference = 'Continue'

Write-Host '=== контейнеры стенда ==='
$running = @(docker ps --filter "name=ohw-runtime" --format '{{.Names}}\t{{.Status}}')
if ($running.Count -eq 0) {
    Write-Host '  ни одного. Стенд не поднят.'
}
else {
    $running | ForEach-Object { Write-Host "  $_" }
}

Write-Host ''
Write-Host "=== тома набора '$Set' ==="
$volumes = @(docker volume ls --format '{{.Name}}' | Where-Object { $_ -like "ohw-runtime_${Set}_*" })
if ($volumes.Count -eq 0) {
    Write-Host '  тома нет. Набор ещё ни разу не поднимался.'
}
else {
    $volumes | Sort-Object | ForEach-Object { Write-Host "  $_" }
}

Write-Host ''
Write-Host '=== наполненность ==='
if (-not $Neo4jPassword) {
    Write-Host '  NEO4J_PASSWORD не задан - количество узлов НЕ измерено.'
    Write-Host '  Это «не измерено», а не «узлов нет». Задайте пароль и повторите.'
    return
}

$query = 'MATCH (n) RETURN labels(n)[0] AS label, count(*) AS c ORDER BY c DESC;'
$rows = docker exec ohw-runtime-neo4j cypher-shell -u neo4j -p $Neo4jPassword --format plain $query
if ($LASTEXITCODE -ne 0) {
    Write-Host '  Neo4j не отвечает. Это не «база пуста», это «базы нет».'
    Write-Host '  Поднять: powershell -File Demo\run_demo.ps1 -Set ' + $Set
    return
}

$data = @($rows | Select-Object -Skip 1 | Where-Object { $_.Trim() -ne '' })
if ($data.Count -eq 0) {
    Write-Host '  база пуста: нод нет ни одного. Для показа это состояние «пустой стенд».'
}
else {
    foreach ($line in $data) { Write-Host "  $line" }
    $ctx = [regex]::Match(($data -join ' '), 'ContextNode,\s*(\d+)')
    if ($ctx.Success -and [int]$ctx.Groups[1].Value -eq 0) {
        Write-Warning 'Chunk есть, а ContextNode нет - это vector-only загрузка (ADR-053).'
        Write-Warning 'Джоба при этом завершается succeeded, то есть выглядит как успех.'
    }
}