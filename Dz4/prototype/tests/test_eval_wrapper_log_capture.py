"""Форма перехвата вывода раннера в run_eval_run.ps1 (хостовый PowerShell).

Поведение здесь не проверяется и проверено быть не может: гейт крутится в
Linux-контейнере, PowerShell в него не входит. Поэтому этот тест держит только
форму — те же обязательные условия, без которых перехват ломает прогон:

  * политика ошибок понижается на время вызова и восстанавливается в `finally`
    (иначе `2>&1` + `Stop` роняет скрипт terminating-ошибкой на первой строке
    stderr, и `$runExit` не присваивается вовсе);
  * каждый объект приводится к строке явно (иначе в лог падает
    форматированный ErrorRecord с CategoryInfo);
  * код возврата берётся из вызова, лог пишется в UTF-8 без BOM.

Каждому пункту соответствует проверка поведения в
`infra/scripts/test_eval_log_capture.ps1` (18 утверждений, хостовый запуск).
Этот тест ловит откат форму, а не семантики: правку, которая сохранит вид, но
сломает перехват, он пропустит.
"""

from __future__ import annotations

from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "infra" / "scripts"
CAPTURE = SCRIPTS / "Invoke-Captured.ps1"
WRAPPER = SCRIPTS / "run_eval_run.ps1"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def test_capture_lowers_error_preference_and_restores_it() -> None:
    """`Stop` + `2>&1` на stderr нативной команды убивает скрипт — это главная ловушка."""
    text = _read(CAPTURE)
    assert "$ErrorActionPreference = 'Continue'" in text
    assert "$ErrorActionPreference = $previous" in text
    finally_block = text.split("finally", 1)[1]
    assert "$ErrorActionPreference = $previous" in finally_block


def test_capture_stringifies_pipeline_objects() -> None:
    """Без `"$_"` в лог попадает ErrorRecord вместе с CategoryInfo."""
    text = _read(CAPTURE)
    assert '$text = "$_"' in text
    assert "$writer.WriteLine($text)" in text


def test_capture_reads_exit_code_from_the_call() -> None:
    """Код возврата читается после вызова, а не из конвейера."""
    text = _read(CAPTURE)
    assert "& $Command 2>&1" in text
    assert "$exitCode = $LASTEXITCODE" in text


def test_capture_writes_utf8_without_bom_and_flushes() -> None:
    """BOM в логе Python — мусор; без AutoFlush лог не читается во время прогона."""
    text = _read(CAPTURE)
    assert "System.Text.UTF8Encoding($false)" in text
    assert "$writer.AutoFlush = $true" in text


def test_wrapper_captures_runner_output_to_runner_log() -> None:
    """Wrapper обязан писать перехват в logs/runner.log, а не звать compose напрямую."""
    text = _read(WRAPPER)
    assert "runner.log" in text
    assert "Invoke-Captured" in text
    run_call = "Invoke-Compose 'run' '--rm' '--no-deps' 'eval-runner' 'python' @evalTail"
    assert run_call in text
    # Прямой вызов compose вне scriptblock означал бы, что перехват обойдён.
    captured = text.split("-Command {", 1)
    assert len(captured) == 2, "вызов раннера должен быть внутри -Command {}"
    assert run_call in captured[1].split("}", 1)[0]


def test_wrapper_splats_eval_tail() -> None:
    """`@evalTail`, а не `@($evalTail)`: array subexpression схлопывает срез в ОДИН аргумент.

    Минус-проверку «паттерна нет в тексте» сознательно не делаем: wrapper
    упоминает `@($evalArgs[1..N])` в комментарии-предупреждении, и поиск по сырому
    тексту ловит прозу, а не код.
    """
    text = _read(WRAPPER)
    assert "$evalTail = $evalArgs[1..($evalArgs.Count - 1)]" in text
    assert "eval-runner' 'python' @evalTail" in text
