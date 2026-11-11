"""Tests for stage selection and the file-based workflow."""

import json
from pathlib import Path

import pytest

from src.generator.models import CaseType, TestCase
from src.models import PipelineData, QualityMetrics, ScenarioResult
from src.workflow.main import EXIT_USAGE, build_parser, main, selected_phases


def test_default_phases_are_all() -> None:
    args = build_parser().parse_args([])
    assert selected_phases(args) == ["generate", "assess", "visualize", "report"]


def test_explicit_phases_are_ordered() -> None:
    args = build_parser().parse_args(["--report", "--generate"])
    assert selected_phases(args) == ["generate", "report"]


def test_runs_must_be_positive() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--runs", "0"])
    assert exc.value.code == EXIT_USAGE


def _snapshot(tmp_path: Path) -> PipelineData:
    case = TestCase(
        id="TC-01",
        type=CaseType.POSITIVE,
        title="Успех",
        steps=["Шаг"],
        expected="Результат",
        requirement="BR-1",
    )
    scenario = ScenarioResult(
        filename="req.md",
        source_path="input/req.md",
        artifact_type="testcases",
        output_file=str(tmp_path / "scenarios" / "req.md"),
        rendered=(
            "# Тест-кейсы\n\nBR-1. Система должна проверять форму. "
            "Ожидаем успешный результат."
        ),
        generations=[
            "# Тест-кейсы\n\nBR-1. Система должна проверять форму. "
            "Ожидаем успешный результат."
        ],
        cases=[case],
        prompt="prompt",
        attempts=1,
        quality=QualityMetrics(),
        requirement_ids=["BR-1"],
        covered_requirements=[],
        prompt_sha256="abc",
        runs=1,
    )
    data = PipelineData(
        input_dir="input",
        artifact_type="testcases",
        model="qwen2.5:7b-instruct",
        runs=1,
        scenarios=[scenario],
    )
    (tmp_path / "generation.json").write_text(
        json.dumps(data.to_snapshot_dict(), ensure_ascii=False), encoding="utf-8"
    )
    return data


def test_assess_and_report_from_snapshot(tmp_path: Path) -> None:
    _snapshot(tmp_path)
    assert main(["--assess", "--output-dir", str(tmp_path)]) == 0
    report = json.loads(
        (tmp_path / "quality-metrics.json").read_text(encoding="utf-8")
    )
    assert report["scenarios"][0]["quality"]["overall"] is not None
    assert report["scenarios"][0]["case_summaries"][0]["id"] == "TC-01"

    assert main(["--report", "--output-dir", str(tmp_path)]) == 0
    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "TC-01" in markdown
    assert "req.md" in markdown


def test_assess_without_snapshot_returns_usage_error(tmp_path: Path) -> None:
    assert main(["--assess", "--output-dir", str(tmp_path)]) == EXIT_USAGE


def test_fail_under_returns_quality_exit_code(tmp_path: Path) -> None:
    _snapshot(tmp_path)
    assert main(["--assess", "--output-dir", str(tmp_path)]) == 0
    code = main(
        [
            "--report",
            "--output-dir",
            str(tmp_path),
            "--fail-under",
            "1.0",
        ]
    )
    assert code == 5
