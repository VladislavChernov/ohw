from pathlib import Path

import pytest
from conftest import (
    FakeChat,
    balanced_set,
    make_raw_cases,
    patch_client,
    write_input,
)

from ai_testgen.cli import EXIT_GENERATION, EXIT_OK, EXIT_USAGE, main

CHECKLIST_OK = (
    "## Зона одна\n\n"
    "- проверка один\n"
    "## Зона две\n\n"
    "- проверка два\n"
)

PLAN_OK = "\n\n".join(
    f"## {name}\n\nСодержимое раздела {name}." for name in ("Цели тестирования", "Объём тестирования", "Подход и виды тестирования", "Критерии начала и окончания тестирования", "Риски")
)


def test_mixed_batch_three_types_three_outputs(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "m")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.chdir(input_dir.parent)
    write_input(input_dir, "testcases_txt.txt", "cases for https://x.com")
    write_input(input_dir, "checklist_md.md", "---\ntype: checklist\n---\nzones for https://x.com")
    write_input(input_dir, "test_plan_md.md", "---\ntype: testplan\n---\nplan for https://x.com")

    fake = FakeChat([CHECKLIST_OK, PLAN_OK, make_raw_cases(balanced_set(10))])
    patch_client(monkeypatch, fake)

    code = main(["--count", "10", "--input-dir", str(input_dir), "--output-dir", str(output_dir)])

    assert code == EXIT_OK
    assert len(fake.calls) == 3

    cases_report = (output_dir / "testcases_txt.md").read_text(encoding="utf-8")
    assert cases_report.startswith("# Тест-кейсы:")
    assert cases_report.count("### TC-") == 10

    checklist_report = (output_dir / "checklist_md.md").read_text(encoding="utf-8")
    assert checklist_report.startswith("# Чек-лист:")
    assert "## Зона одна" in checklist_report

    plan_report = (output_dir / "test_plan_md.md").read_text(encoding="utf-8")
    assert plan_report.startswith("# Тест-план:")
    assert "## Риски" in plan_report


def test_json_mode_flag_per_type(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "m")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.chdir(input_dir.parent)
    write_input(input_dir, "testcases_txt.txt", "cases https://x.com")
    write_input(input_dir, "checklist_md.md", "---\ntype: checklist\n---\nzones")

    fake = FakeChat([CHECKLIST_OK, make_raw_cases(balanced_set(10))])
    patch_client(monkeypatch, fake)

    code = main(["--count", "10", "--input-dir", str(input_dir), "--output-dir", str(output_dir)])

    assert code == EXIT_OK
    assert fake.json_modes == [False, True]


def test_markdown_retry_uses_markdown_feedback_prompt(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "m")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.chdir(input_dir.parent)
    write_input(input_dir, "checklist_md.md", "---\ntype: checklist\n---\nzones")

    bad = '{"areas": []}'
    fake = FakeChat([bad, CHECKLIST_OK])
    patch_client(monkeypatch, fake)

    code = main(["--count", "10", "--input-dir", str(input_dir), "--output-dir", str(output_dir)])

    assert code == EXIT_OK
    assert len(fake.calls) == 2
    assert "Markdown document only" in fake.calls[1]


def test_markdown_retries_exhausted_exit_4(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "m")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.chdir(input_dir.parent)
    write_input(input_dir, "test_plan_md.md", "---\ntype: testplan\n---\nplan")

    fake = FakeChat(["no structure here"] * 3)
    patch_client(monkeypatch, fake)

    code = main(
        [
            "--count",
            "10",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--max-retries",
            "2",
        ]
    )

    assert code == EXIT_GENERATION
    assert len(fake.calls) == 3
    assert not output_dir.exists() or not any(output_dir.iterdir())


def test_unknown_front_matter_type_exit_2_before_llm(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "m")
    monkeypatch.chdir(input_dir.parent)
    write_input(input_dir, "doc.md", "---\ntype: slides\n---\nbody")

    fake = FakeChat([])
    patch_client(monkeypatch, fake)

    code = main(["--count", "10", "--input-dir", str(input_dir), "--output-dir", str(output_dir)])

    assert code == EXIT_USAGE
    assert fake.calls == []
