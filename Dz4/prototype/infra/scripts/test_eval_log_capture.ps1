<#
.SYNOPSIS
    Проверяет перехват вывода раннера: код возврата, чистоту лога, UTF-8 без BOM,
    восстановление политики ошибок. Крутится на хосте, в гейт не входит.

.DESCRIPTION
    Тест бьёт по рабочему коду (`Invoke-Captured.ps1`), а не по копии логики:
    копия неизбежно разойдётся с оригиналом, и тест станет зелёным на мёртвом
    коде. Поэтому подключается тот же файл, что и wrapper.

    Каждый случай закрывает конкретную ловушку, найденную при перехвате вывода
    eval-раннера; комментарий в случае — это и есть его утверждение.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File test_eval_log_capture.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
# Иначе отчёт о сбоях сам приходит в ANSI-кодировке консоли и читается как мусор:
# непонятно, что именно упало. Тот же эффект даёт `git show` (NEXT_SESSION, п.4.2).
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $scriptDir 'Invoke-Captured.ps1')

$failed = 0
$passed = 0

function Assert-True {
    param([string]$Name, [bool]$Condition, [string]$Detail = '')
    if ($Condition) {
        $script:passed++
        Write-Host "  ок   $Name"
    } else {
        $script:failed++
        Write-Host "  СБОЙ $Name" -ForegroundColor Red
        if ($Detail) { Write-Host "       $Detail" -ForegroundColor Red }
    }
}

$workDir = Join-Path ([System.IO.Path]::GetTempPath()) ("logcap-" + [Guid]::NewGuid().ToString('N'))
[void][System.IO.Directory]::CreateDirectory($workDir)

try {
    # --- случай 1: stdout и stderr оба попали в лог, код 0 --------------------
    Write-Host '1. обе строки в логе, успешная команда'
    $log1 = Join-Path $workDir 'run1.log'
    $code = Invoke-Captured -LogPath $log1 -Header @('# hdr') -Command {
        cmd.exe /c "echo line-stdout & echo line-stderr 1>&2 & exit 0"
    }
    # Сравниваем по содержимому, без хвостовых пробелов: PowerShell 5.1 передаёт
    # аргументы в `cmd /c "..."` с лишним пробелом, и тот попадает в stdout
    # процесса. Проверять побайтовую длину строки здесь нельзя, а содержание — можно.
    $body = @(Get-Content -LiteralPath $log1 -Encoding UTF8 | ForEach-Object { $_.TrimEnd() })
    Assert-True 'код возврата 0' ($code -eq 0) "получено $code"
    Assert-True 'stdout в логе' ($body -contains 'line-stdout')
    Assert-True 'stderr в логе' ($body -contains 'line-stderr')
    Assert-True 'заголовок в логе' ($body -contains '# hdr')

    # --- случай 2: ненулевой код дошёл (регрессия на ловушку Tee-Object) -------
    Write-Host '2. ненулевой код возврата не теряется конвейером'
    $log2 = Join-Path $workDir 'run2.log'
    $code = Invoke-Captured -LogPath $log2 -Command {
        cmd.exe /c "echo before-exit & exit 7"
    }
    Assert-True 'код возврата 7' ($code -eq 7) "получено $code"

    # --- случай 3: stderr при $ErrorActionPreference = Stop не роняет скрипт --
    Write-Host '3. stderr не роняет скрипт при ErrorActionPreference = Stop'
    $log3 = Join-Path $workDir 'run3.log'
    $threw = $false
    $code = 0
    try {
        $code = Invoke-Captured -LogPath $log3 -Command {
            cmd.exe /c "echo out-before & echo err-before 1>&2 & exit 5"
        }
    } catch {
        $threw = $true
        $threwMessage = $_.Exception.GetType().Name
    }
    Assert-True 'исключения не было' (-not $threw) $(if ($threw) { "бросил $threwMessage" } else { '' })
    Assert-True 'код возврата 5' ($code -eq 5) "получено $code"
    $body3 = @(Get-Content -LiteralPath $log3 -Encoding UTF8 | ForEach-Object { $_.TrimEnd() })
    Assert-True 'строка после stderr дошла до лога' ($body3 -contains 'err-before')
    Assert-True 'нет форматированного ErrorRecord' (-not ($body3 -match 'CategoryInfo'))
    Assert-True 'нет FullyQualifiedErrorId' (-not ($body3 -match 'FullyQualifiedErrorId'))

    # --- случай 4: политика ошибок вызывающего восстановлена ------------------
    Write-Host '4. политика ошибок вызывающего не изменена'
    $ErrorActionPreference = 'Stop'
    [void](Invoke-Captured -LogPath (Join-Path $workDir 'run4.log') -Command { cmd.exe /c "echo x 1>&2 & exit 0" })
    Assert-True 'Stop на месте' ($ErrorActionPreference -eq 'Stop') "получено $ErrorActionPreference"

    # --- случай 5: лог в UTF-8 без BOM ----------------------------------------
    Write-Host '5. лог без BOM, читается как UTF-8'
    $bytes = [System.IO.File]::ReadAllBytes($log1)
    $hasBom = $bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF
    Assert-True 'BOM нет' (-not $hasBom)
    $nonAscii = [System.Text.Encoding]::UTF8.GetString($bytes)
    Assert-True 'кириллица в логе не побилась' ($nonAscii -match 'логе|вывод')

    # --- случай 6: каталог лога создаётся, повторный прогон перезаписывает ------
    Write-Host '6. каталог создаётся, лог перезаписывается'
    $log6 = Join-Path (Join-Path $workDir 'nested/deep') 'run6.log'
    [void](Invoke-Captured -LogPath $log6 -Command { cmd.exe /c "echo once & exit 0" })
    [void](Invoke-Captured -LogPath $log6 -Command { cmd.exe /c "echo twice & exit 0" })
    $body6 = @(Get-Content -LiteralPath $log6 -Encoding UTF8 | ForEach-Object { $_.TrimEnd() })
    Assert-True 'лог создан во вложенном каталоге' (Test-Path -LiteralPath $log6)
    Assert-True 'прошлый прогон не остался в логе' (-not ($body6 -contains 'once'))
    Assert-True 'новый вывод на месте' ($body6 -contains 'twice')
    # --- случай 7: команда видит переменные вызывающей области ----------------
    # Wrapper собирает аргументы раннера в `$evalTail` и зовёт `Invoke-Compose` из
    # scriptblock'а. Если такая видимость не работает, обёртка падает уже на первом
    # прогоне, поэтому это проверяется здесь, а не в бою.
    Write-Host '7. команда видит переменные и функции вызывающей области'
    $evalTail = @('echo', 'tail-visible')
    $log7 = Join-Path $workDir 'run7.log'
    $code = Invoke-Captured -LogPath $log7 -Command {
        cmd.exe /c $evalTail
    }
    $body7 = @(Get-Content -LiteralPath $log7 -Encoding UTF8 | ForEach-Object { $_.TrimEnd() })
    Assert-True 'код возврата 0' ($code -eq 0) "получено $code"
    Assert-True 'аргумент из внешней области дошёл' ($body7 -contains 'tail-visible')
} finally {
    if (Test-Path -LiteralPath $workDir) {
        Remove-Item -LiteralPath $workDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host ''
Write-Host "итого: ок $passed, сбоев $failed"
if ($failed -gt 0) { exit 1 }
exit 0
