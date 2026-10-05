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
COMPOSE = SCRIPTS.parent / "compose.eval-minimal.yaml"


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

def test_wrapper_passes_the_script_path_to_python() -> None:
    """Путь к `run_eval.py` обязан доходить до python.

    Регрессия, найдена 2026-10-05: обёртка резала `$evalArgs[1..N]`, отбрасывая первый
    элемент — путь к скрипту, — и прогон падал с `unknown option --domain`, потому что
    первым аргументом к python приходили флаги.

    Предыдущая проверка утверждала, что в тексте обёртки есть ровно строка
    `$evalTail = $evalArgs[1..($evalArgs.Count - 1)]`. Это проверка формы, а не поведения:
    она проходила на сломанной обёртке именно потому, что не запускала её. Здесь проверяется
    инвариант — «скрипт доходит до python» — вместе с его причиной.

    Причина в том, что `docker compose run <svc> <команда...>` **перекрывает** `command` сервиса.
У `eval-runner` в compose есть безопасный дефолт
`command: ["python", "/app/infra/eval/run_eval.py", "--help"]`, но обёртка идёт через
`docker compose run ... eval-runner python <args>`, и этот дефолт отбрасывается целиком: до
python доходит только `<args>`. Проверено на прогоне: без пути к скрипту первым аргументом
приходил `--domain`, и python отвечал `unknown option --domain`.
    """
    wrapper = _read(WRAPPER)
    assignments = [
        line.strip()
        for line in wrapper.splitlines()
        if line.strip().startswith("$evalTail")
    ]
    assert assignments == ["$evalTail = $evalArgs"], (
        f"ожидалась одна строка присваивания без среза, найдено: {assignments}"
    )
    assert "$evalArgs = @('/app/infra/eval/run_eval.py'" in wrapper, "скрипт первый аргументом"
    assert "eval-runner' 'python' @evalTail" in wrapper, "splatting по переменной обязателен"

    compose = _read(COMPOSE)
    assert (
        'command: ["python", "/app/infra/eval/run_eval.py", "--help"]' in compose
    ), "безопасный дефолт сервиса перекрывается docker compose run, и это обязано быть видимо"
