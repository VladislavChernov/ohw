<#
.SYNOPSIS
    Выполняет нативную команду, пишет её вывод в лог по ходу и возвращает код
    возврата команды.

.DESCRIPTION
    Вынесено из run_eval_run.ps1 отдельным файлом не для красоты: перехват вывода
    раннера ломается двумя способами, и оба надо проверять прогоном, а не
    пересказом в комментарии. Проверено на Windows PowerShell 5.1.

    1. **Конвей не затирает код возврата.** `Tee-Object` — cmdlet, а
       `$LASTEXITCODE` выставляет только нативная команда, поэтому после
       `docker ... | Tee-Object` переменная всё ещё содержит код docker.
       Ловушка, записанная ранее в NEXT_SESSION_INSTRUCTIONS («конвейер делает
       $LASTEXITCODE отражением Tee-Object»), была неверной — проверено.

    2. **Настоящая ловушка — `$ErrorActionPreference = 'Stop'` вместе с `2>&1`.**
       Слияние stderr нативной команды в поток вывода превращает строки stderr в
       ErrorRecord, и при `Stop` первая же строка роняет скрипт terminating
       ошибкой. Обёртка прогона ставит именно `Stop`, поэтому перехват без
       понижения политики убил бы прогон на первой строке stderr: `$runExit` не
       был бы присвоен, не появились бы ни лог, ни resources.json.

    3. **ErrorRecord в лог писать нельзя.** Без явного приведения к строке в лог
       попадает форматированная запись с `CategoryInfo` / `FullyQualifiedErrorId`
       и обрывком самой команды — в отладочном логе это мусор.

    Отсюда четыре обязательных условия, все здесь и соблюдены: понизить политику
    ошибок на время вызова и восстановить её в `finally`; привести каждый объект
    к строке; писать лог в UTF-8 без BOM; брать `$LASTEXITCODE` из вызова.

    Ограничение: это хостовый PowerShell, в гейт (pytest в Linux) он не попадает.
    Поведение проверяется `test_eval_log_capture.ps1`, форма — тестом
    `tests/test_eval_wrapper_log_capture.py`.

.PARAMETER Command
    Scriptblock с нативным вызовом. Выполняется как есть, поток вывода и stderr
    сливаются и пишутся в лог.

.PARAMETER LogPath
    Файл лога. Перезаписывается. UTF-8 без BOM, построчно, с автосбросом, чтобы
    лог был читаем во время прогона, а не после.

.PARAMETER Header
    Строки заголовка, которые пишутся в лог до вывода команды. Их смысл —
    паспорт прогона рядом с выводом: по одному логу должно быть видно, какой
    код и какое дерево его породили.

.OUTPUTS
    Исходный код возврата команды. Ничего больше: строки команды уходят в
    конвейер `ForEach-Object` и в лог, в выходной поток функции не попадают.
#>
[CmdletBinding()]
param()

function Invoke-Captured {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock]$Command,
        [Parameter(Mandatory = $true)]
        [string]$LogPath,
        [string[]]$Header = @()
    )

    $parent = Split-Path -Parent $LogPath
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        [void][System.IO.Directory]::CreateDirectory($parent)
    }

    # UTF-8 без BOM: лог читают глазами и grep'ом, а BOM в логе Python — мусор.
    $encoding = New-Object System.Text.UTF8Encoding($false)
    $writer = New-Object System.IO.StreamWriter($LogPath, $false, $encoding)
    $writer.AutoFlush = $true

    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $exitCode = 0
    try {
        $writer.WriteLine("# $(Get-Date -Format o)")
        foreach ($line in $Header) { $writer.WriteLine($line) }
        $writer.WriteLine('# --- вывод команды ---')
        # Приведение к строке обязательно: см. пункт 3 в описании.
        & $Command 2>&1 | ForEach-Object {
            $text = "$_"
            Write-Host $text
            $writer.WriteLine($text)
        }
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previous
        $writer.Dispose()
    }
    return $exitCode
}
