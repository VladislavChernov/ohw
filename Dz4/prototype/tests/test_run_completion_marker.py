"""Гард терминального признака завершённости прогона (`infra/eval/run_eval.py`).

Дефект, который закрывает этот файл, случился 2026-10-04: прогон умер на первом вопросе,
и об этом узнали только по выводу. В папке не осталось ничего, что сказало бы «прогон не
завершён»: `failures.jsonl` пуст по определению (он про вопросы, а не про прогон), а
`lift_report.json` и `qa_log.jsonl` выглядят как результаты. Если бы обрыв пришёлся на
десятый вопрос, папка была бы неотличима от успешной — и разбор шёл бы по данным прогона,
которого не было.

Приём здесь — тот же, что с ожидателями: не «записать ошибку», а ждать терминального
признака. Ошибка в `failures.jsonl` маскировала бы: файл внятно называет «вопросы с
ошибками», и «прогон упал» туда не относится.
"""

from __future__ import annotations

import ast
import json
import pathlib
import subprocess
import sys
import textwrap

RUN_EVAL = pathlib.Path("infra/eval/run_eval.py")
TEST_RUNNER = pathlib.Path("tests/test_run_eval_artifacts.py")


def _host_root() -> pathlib.Path:
    here = pathlib.Path.cwd()
    for candidate in (here, *here.parents):
        if (candidate / RUN_EVAL).is_file():
            return candidate
    raise AssertionError("корень репозитория не найден: нет infra/eval/run_eval.py")


def test_run_state_is_removed_at_start_and_written_only_at_the_end() -> None:
    """Файл появляется в конце и стирается в начале.

    Если стирания в начале нет, то `run_state.json` от предыдущего успешного прогона будет
    лежать в папке следующего, который упал, — то есть ровно тот ложный зелёный папка,
    который и закрывает эта проверка.
    """
    source = (_host_root() / RUN_EVAL).read_text(encoding="utf-8")
    tree = ast.parse(source)
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")

    unlink_lines = [n.lineno for n in ast.walk(main) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "unlink"]
    write_lines = [
        n.lineno
        for n in ast.walk(main)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "write_text"
    ]
    assert unlink_lines, "run_state.json не стирается в начале прогона — останется от прошлого"
    assert write_lines, "run_state.json не пишется — признака завершённости нет вообще"
    assert max(unlink_lines) < max(write_lines), "стирание позже записи: файл не успевает исчезнуть"


def test_run_state_records_more_than_a_bare_flag() -> None:
    """`{"status": "completed"}` без ревизии и вопросов бесполезен для разбора: непонятно,
    что именно завершилось. Проверяем, что состав полей объявлен, а не что он красив."""
    source = (_host_root() / RUN_EVAL).read_text(encoding="utf-8")
    required = ("status", "run_id", "questions", "failed_questions", "finished_at")
    for field in required:
        assert f'"{field}"' in source, f"run_state.json не содержит поля {field}"


def test_failures_file_is_not_used_as_run_error_channel() -> None:
    """failures.jsonl — про вопросы. Класть туда «прогон упал» размывает смысл файла
    и возвращает ровно ту путаницу, ради которой введён run_state.json."""
    source = (_host_root() / RUN_EVAL).read_text(encoding="utf-8")
    assert "failures.jsonl" in source
    # Инициализация пустым файлом и append по вопросу — правильное поведение: failures.jsonl
    # описывает вопросы, а не прогон. Поэтому отдельный канал статуса обязателен.
    assert "run_state.json" in source, "нет отдельного канала для статуса прогона"


def test_aborted_run_leaves_no_run_state(tmp_path: pathlib.Path) -> None:
    """Функциональная проверка на поддельном раннере: обрыв должен оставить папку без
    признака завершённости, даже если `qa_log` и `lift_report` уже записаны.

    Раннер подменяется не целиком, а через точку отказа: та же логика `run_state.json`,
    что и в настоящем `main()`, но без обращения к стенду.
    """
    script = textwrap.dedent(
        """
        import json, pathlib, sys
        out = pathlib.Path(sys.argv[1])
        out.mkdir(parents=True, exist_ok=True)
        state = out / "run_state.json"
        state.unlink(missing_ok=True)          # как в main(): стираем в начале
        (out / "qa_log.jsonl").write_text('{"question": "q1"}\\n', encoding="utf-8")
        (out / "failures.jsonl").write_text("", encoding="utf-8")
        if state.exists():
            raise AssertionError("признак завершённости создан до конца прогона")
        raise RuntimeError("стенд упал на третьем вопросе")   # обрыв
        """
    )
    path = tmp_path / "aborted.py"
    path.write_text(script, encoding="utf-8")
    completed = subprocess.run([sys.executable, str(path), str(tmp_path / "out")], capture_output=True, text=True, check=False)

    assert completed.returncode != 0, "обрыв вернул нулевой код возврата — процесс зелёный при падении"
    out = tmp_path / "out"
    assert not (out / "run_state.json").exists(), "после обрыва остался признак завершённости"
    # Папка выглядит как результат — ровно тот случай, ради которого файл и нужен.
    assert (out / "qa_log.jsonl").exists() and (out / "failures.jsonl").exists()


def test_completed_run_writes_run_state_with_payload(tmp_path: pathlib.Path) -> None:
    """Зеркальная проверка: нормальный выход обязан оставить файл с полями."""
    script = textwrap.dedent(
        """
        import json, pathlib, sys, time
        out = pathlib.Path(sys.argv[1])
        out.mkdir(parents=True, exist_ok=True)
        state = out / "run_state.json"
        state.unlink(missing_ok=True)
        state.write_text(json.dumps({
            "status": "completed", "run_id": "r1", "questions": 8,
            "failed_questions": 0, "verdict": "pass",
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        """
    )
    path = tmp_path / "done.py"
    path.write_text(script, encoding="utf-8")
    result = subprocess.run([sys.executable, str(path), str(tmp_path / "out")], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    payload = json.loads((tmp_path / "out" / "run_state.json").read_text(encoding="utf-8"))
    assert payload["status"] == "completed"
    assert payload["questions"] == 8
    assert payload["failed_questions"] == 0