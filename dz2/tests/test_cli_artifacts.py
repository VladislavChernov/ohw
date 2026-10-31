import json
from pathlib import Path
from typing import Any

import pytest
from conftest import (
    FakeChat,
    balanced_set,
    make_raw_cases,
    patch_client,
    write_input,
)

from ai_testgen.cli import (
    EXIT_EVALUATION,
    EXIT_GENERATION,
    EXIT_OK,
    EXIT_USAGE,
    main,
)
from ai_testgen.prompt import QAREPORT_SECTIONS

CHECKLIST_OK = (
    "## Зона одна\n\n"
    "- проверка один\n"
    "## Зона две\n\n"
    "- проверка два\n"
)

PLAN_OK = "\n\n".join(
    f"## {name}\n\nСодержимое раздела {name}." for name in ("Цели тестирования", "Объём тестирования", "Подход и виды тестирования", "Критерии начала и окончания тестирования", "Риски")
)

QA_OK = "\n\n".join(
    f"## {name}\n\nСодержимое раздела {name}." for name in QAREPORT_SECTIONS
)

MATRIX_OK = (
    "## Матрица покрытия требований\n\n"
    "| Requirement ID | Requirement Description | Test Case IDs | Coverage Status | Notes |\n"
    "|---|---|---|---|---|\n"
    "| BR-1 | login | TC-01 | Covered | - |\n"
)


def _quality_payload(output_dir: Path, stem: str) -> dict[str, Any]:
    """Read the one timestamped score file written for ``stem``."""
    files = sorted((output_dir / "quality").glob(f"{stem}-*.json"))
    assert len(files) == 1, files
    return json.loads(files[0].read_text(encoding="utf-8"))


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


def test_mixed_batch_five_types_five_outputs(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "m")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    monkeypatch.chdir(input_dir.parent)
    write_input(input_dir, "a_checklist.md", "---\ntype: checklist\n---\nzones https://x.com")
    write_input(input_dir, "b_matrix.md", "---\ntype: coverage_matrix\n---\nBR-1 https://x.com")
    write_input(input_dir, "c_qa.md", "---\ntype: qa_report\n---\nBR-1 https://x.com")
    write_input(input_dir, "d_cases.txt", "cases for https://x.com")
    write_input(input_dir, "e_plan.md", "---\ntype: testplan\n---\nplan https://x.com")

    fake = FakeChat([CHECKLIST_OK, MATRIX_OK, QA_OK, make_raw_cases(balanced_set(10)), PLAN_OK])
    patch_client(monkeypatch, fake)

    code = main(["--count", "10", "--input-dir", str(input_dir), "--output-dir", str(output_dir)])

    assert code == EXIT_OK
    assert len(fake.calls) == 5
    assert (output_dir / "a_checklist.md").read_text(encoding="utf-8").startswith("# Чек-лист:")
    assert (output_dir / "b_matrix.md").read_text(encoding="utf-8").startswith("# Матрица покрытия:")
    assert (output_dir / "c_qa.md").read_text(encoding="utf-8").startswith("# QA-отчёт:")
    assert (output_dir / "d_cases.md").read_text(encoding="utf-8").startswith("# Тест-кейсы:")
    assert (output_dir / "e_plan.md").read_text(encoding="utf-8").startswith("# Тест-план:")


def test_prompt_dir_template_is_sent_to_llm(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "m")
    monkeypatch.chdir(input_dir.parent)
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "testcases.txt").write_text(
        "SENTINEL TEMPLATE\nRequirements:\n{content}", encoding="utf-8"
    )
    write_input(input_dir, "doc.md", "cases for https://x.com")

    fake = FakeChat([make_raw_cases(balanced_set(10))])
    patch_client(monkeypatch, fake)

    code = main(
        [
            "--count",
            "10",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--prompt-dir",
            str(prompts),
        ]
    )

    assert code == EXIT_OK
    assert fake.calls[0].startswith("SENTINEL TEMPLATE")


def test_missing_prompt_dir_exits_2_before_llm(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "m")
    monkeypatch.chdir(input_dir.parent)
    write_input(input_dir, "doc.md", "cases https://x.com")

    fake = FakeChat([])
    patch_client(monkeypatch, fake)

    code = main(
        [
            "--count",
            "10",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--prompt-dir",
            str(tmp_path / "missing"),
        ]
    )

    assert code == EXIT_USAGE
    assert fake.calls == []


def test_type_override_ignores_front_matter(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "m")
    monkeypatch.chdir(input_dir.parent)
    write_input(input_dir, "doc.md", "---\ntype: testplan\n---\nzones https://x.com")

    fake = FakeChat([CHECKLIST_OK])
    patch_client(monkeypatch, fake)

    code = main(
        [
            "--count",
            "10",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--type",
            "checklist",
        ]
    )

    assert code == EXIT_OK
    assert len(fake.calls) == 1
    report = (output_dir / "doc.checklist.md").read_text(encoding="utf-8")
    assert report.startswith("# Чек-лист:")


def test_all_types_generates_five_outputs_for_one_doc(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "m")
    monkeypatch.chdir(input_dir.parent)
    write_input(input_dir, "doc.md", "BR-1: login https://x.com")

    fake = FakeChat(
        [
            make_raw_cases(balanced_set(10)),
            CHECKLIST_OK,
            PLAN_OK,
            QA_OK,
            MATRIX_OK,
        ]
    )
    patch_client(monkeypatch, fake)

    code = main(
        [
            "--count",
            "10",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--all-types",
        ]
    )

    assert code == EXIT_OK
    assert fake.json_modes == [True, False, False, False, False]
    written = sorted(path.name for path in output_dir.iterdir())
    assert written == [
        "doc.checklist.md",
        "doc.coveragematrix.md",
        "doc.qareport.md",
        "doc.testcases.md",
        "doc.testplan.md",
    ]
    # The matrix is the last call and must reuse ids generated earlier in the run.
    matrix_prompt = fake.calls[-1]
    assert "Test cases already generated for these requirements:" in matrix_prompt
    assert "- TC-01 (negative, no requirement): title TC-01" in matrix_prompt
    assert "- TC-10 (positive, no requirement): title TC-10" in matrix_prompt
    assert "No test cases have been generated" not in matrix_prompt


def test_matrix_without_generated_testcases_does_not_invite_invented_ids(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "m")
    monkeypatch.chdir(input_dir.parent)
    write_input(input_dir, "doc.md", "BR-1: login https://x.com")

    fake = FakeChat([MATRIX_OK])
    patch_client(monkeypatch, fake)

    code = main(
        [
            "--count",
            "10",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--type",
            "coveragematrix",
        ]
    )

    assert code == EXIT_OK
    prompt = fake.calls[0]
    assert "No test cases have been generated for these requirements yet." in prompt
    assert "TC-01" not in prompt


def test_testcases_file_feeds_matrix_prompt(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "m")
    monkeypatch.chdir(input_dir.parent)
    write_input(input_dir, "doc.md", "BR-1: login https://x.com")
    brief = tmp_path / "brief.txt"
    brief.write_text(
        "- TC-07 (positive, BR-3): открытие карточки\n", encoding="utf-8"
    )

    fake = FakeChat([MATRIX_OK])
    patch_client(monkeypatch, fake)

    code = main(
        [
            "--count",
            "10",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--type",
            "coveragematrix",
            "--testcases-file",
            str(brief),
        ]
    )

    assert code == EXIT_OK
    prompt = fake.calls[0]
    assert "Test cases already generated for these requirements:" in prompt
    assert "- TC-07 (positive, BR-3): открытие карточки" in prompt


def test_missing_testcases_file_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "m")
    monkeypatch.chdir(input_dir.parent)
    write_input(input_dir, "doc.md", "BR-1: login https://x.com")

    fake = FakeChat([])
    patch_client(monkeypatch, fake)

    code = main(
        [
            "--count",
            "10",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--type",
            "coveragematrix",
            "--testcases-file",
            str(tmp_path / "nope.txt"),
        ]
    )

    assert code == EXIT_USAGE
    assert fake.calls == []


def test_preset_text_is_sent_to_llm(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "m")
    monkeypatch.chdir(input_dir.parent)
    write_input(input_dir, "doc.md", "BR-1: login https://x.com")

    fake = FakeChat([make_raw_cases(balanced_set(10))])
    patch_client(monkeypatch, fake)

    code = main(
        [
            "--count",
            "10",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--preset",
            "negative-scenarios",
        ]
    )

    assert code == EXIT_OK
    assert "Additional instructions:" in fake.calls[0]
    assert "Cover failure paths explicitly" in fake.calls[0]


def test_unknown_preset_exits_2_before_llm(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "m")
    monkeypatch.chdir(input_dir.parent)
    write_input(input_dir, "doc.md", "BR-1: login https://x.com")

    fake = FakeChat([])
    patch_client(monkeypatch, fake)

    code = main(
        [
            "--count",
            "10",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--preset",
            "nope",
        ]
    )

    assert code == EXIT_USAGE
    assert fake.calls == []


def test_preset_for_wrong_type_exits_2_before_llm(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "m")
    monkeypatch.chdir(input_dir.parent)
    write_input(input_dir, "doc.md", "BR-1: login https://x.com")

    fake = FakeChat([])
    patch_client(monkeypatch, fake)

    code = main(
        [
            "--count",
            "10",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--type",
            "coveragematrix",
            "--preset",
            "localization",
        ]
    )

    assert code == EXIT_USAGE
    assert fake.calls == []


def test_missing_presets_dir_exits_2_before_llm(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "m")
    monkeypatch.chdir(input_dir.parent)
    write_input(input_dir, "doc.md", "BR-1: login https://x.com")

    fake = FakeChat([])
    patch_client(monkeypatch, fake)

    code = main(
        [
            "--count",
            "10",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--presets-dir",
            str(tmp_path / "missing"),
            "--preset",
            "negative-scenarios",
        ]
    )

    assert code == EXIT_USAGE
    assert fake.calls == []

EVALUATION_SCORES = {
    "readability": 4,
    "context": 5,
    "goal": 4,
    "coverage": 3,
}


def judge_answers() -> list[str | Exception]:
    """Four answers, one per criterion: the judge is called once per criterion."""
    return [
        json.dumps(
            {
                "evidence": ["Write a QA report"],
                "score": score,
                "comment": f"«{criterion}» reason",
            },
            ensure_ascii=False,
        )
        for criterion, score in EVALUATION_SCORES.items()
    ]


def test_evaluate_writes_quality_json(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "m")
    monkeypatch.chdir(input_dir.parent)
    write_input(input_dir, "doc.md", "BR-1: login https://x.com")

    fake = FakeChat(judge_answers())
    patch_client(monkeypatch, fake)

    code = main(
        [
            "--count",
            "10",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--type",
            "qareport",
            "--evaluate",
        ]
    )

    assert code == EXIT_OK
    assert fake.json_modes == [True] * 4
    assert fake.temps == [0.0] * 4
    assert "Write a QA report" in fake.calls[0]
    assert all("Write a QA report" in call for call in fake.calls)
    assert not list(output_dir.glob("*.md"))

    payload = _quality_payload(output_dir, "qareport")
    assert payload["source"] == "llm"
    assert payload["scores"] == EVALUATION_SCORES
    assert payload["average"] == 4.0
    assert payload["presets"] == []
    assert payload["model"] == "m"
    assert payload["generated_artifact"] is None


def test_evaluate_links_existing_artifact(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "m")
    monkeypatch.chdir(input_dir.parent)
    write_input(input_dir, "doc.md", "BR-1: login https://x.com")
    artifact = output_dir / "doc.qareport.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("# report\n", encoding="utf-8")

    fake = FakeChat(judge_answers())
    patch_client(monkeypatch, fake)

    code = main(
        [
            "--count",
            "10",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--type",
            "qareport",
            "--evaluate",
        ]
    )

    assert code == EXIT_OK
    payload = _quality_payload(output_dir, "qareport")
    reference = payload["generated_artifact"]
    assert reference["path"] == "doc.qareport.md"
    assert reference["bytes"] == 9
    assert reference["sha256"]
    assert reference["modified_at"]


def test_evaluate_with_preset_uses_preset_filename(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "m")
    monkeypatch.chdir(input_dir.parent)
    write_input(input_dir, "doc.md", "BR-1: login https://x.com")

    fake = FakeChat(judge_answers())
    patch_client(monkeypatch, fake)

    code = main(
        [
            "--count",
            "10",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--type",
            "qareport",
            "--preset",
            "negative-scenarios",
            "--evaluate",
        ]
    )

    assert code == EXIT_OK
    assert "Additional instructions:" in fake.calls[0]
    payload = _quality_payload(output_dir, "qareport-negative-scenarios")
    assert payload["presets"] == ["negative-scenarios"]


def test_evaluate_two_docs_keeps_both_scores(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "m")
    monkeypatch.chdir(input_dir.parent)
    write_input(input_dir, "one.md", "BR-1: login https://x.com")
    write_input(input_dir, "two.md", "BR-1: logout https://x.com")

    fake = FakeChat(judge_answers() * 2)
    patch_client(monkeypatch, fake)

    code = main(
        [
            "--count",
            "10",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--type",
            "qareport",
            "--evaluate",
        ]
    )

    assert code == EXIT_OK
    files = sorted((output_dir / "quality").glob("qareport-*.json"))
    assert len(files) == 2, files


def test_evaluate_invalid_judge_json_exits_5(
    monkeypatch: pytest.MonkeyPatch,
    input_dir: Path,
    output_dir: Path,
) -> None:
    monkeypatch.setenv("OLLAMA_MODEL", "m")
    monkeypatch.chdir(input_dir.parent)
    write_input(input_dir, "doc.md", "BR-1: login https://x.com")

    fake = FakeChat(["not json", "not json", "not json"])
    patch_client(monkeypatch, fake)

    code = main(
        [
            "--count",
            "10",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--type",
            "qareport",
            "--evaluate",
        ]
    )

    assert code == EXIT_EVALUATION
