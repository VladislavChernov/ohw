"""Tests for the JSON and Markdown reports."""

import json
from pathlib import Path

import pytest

from src.config import PIPELINE_VERSION
from src.generator.models import CaseType, TestCase
from src.models import PipelineData, QualityMetrics, ScenarioResult
from src.reporting.report_generator import (
    generate_all_reports,
    load_pipeline_data,
    render_markdown,
)


def _data() -> PipelineData:
    case = TestCase(
        id="TC-01",
        type=CaseType.NEGATIVE,
        title="Неверный email",
        steps=["Ввести email без @", "Отправить форму"],
        expected="Показано сообщение об ошибке",
        requirement="BR-2",
    )
    measured = ScenarioResult(
        filename="registration-requirements.md",
        source_path="input/registration-requirements.md",
        artifact_type="testcases",
        output_file="scenarios/registration-requirements.md",
        rendered="BR-1 BR-2",
        cases=[case],
        prompt="prompt",
        attempts=1,
        quality=QualityMetrics(
            readability=0.75,
            clarity_goals=0.6,
            scenario_coverage=0.5,
            consistency=0.8,
            overall=0.66,
        ),
        requirement_ids=["BR-1", "BR-2"],
        covered_requirements=["BR-1"],
        prompt_sha256="deadbeef",
        runs=1,
    )
    unmeasured = ScenarioResult(
        filename="no-ids.md",
        source_path="input/no-ids.md",
        artifact_type="testcases",
        output_file="scenarios/no-ids.md",
        rendered="Текст без идентификаторов",
        cases=[],
        prompt="prompt",
        attempts=1,
        quality=QualityMetrics(
            readability=0.5,
            clarity_goals=0.4,
            scenario_coverage=None,
            consistency=0.5,
            overall=0.485,
        ),
        requirement_ids=[],
        covered_requirements=[],
        prompt_sha256="cafe",
        runs=1,
    )
    data = PipelineData(
        input_dir="input",
        artifact_type="testcases",
        model="qwen2.5:7b-instruct",
        runs=1,
        generated_at="2026-11-11T10:00:00+00:00",
        scenarios=[measured, unmeasured],
    )
    data.refresh_averages()
    return data


def test_markdown_contains_required_sections() -> None:
    markdown = render_markdown(_data())
    assert "# Отчёт о качестве генерации тестовой документации" in markdown
    assert "## Общая информация" in markdown
    assert "## Распределение качества" in markdown
    assert "## Список сценариев" in markdown
    assert "## Визуальный отчёт" in markdown
    assert "## Источник данных" in markdown


def test_markdown_prints_na_for_missing_metric() -> None:
    markdown = render_markdown(_data())
    assert "| Scenario coverage | 0.500 | 50.0% |" in markdown
    assert "n/a" in markdown


def test_markdown_contains_real_cases_and_requirements() -> None:
    markdown = render_markdown(_data())
    assert "TC-01" in markdown
    assert "Неверный email" in markdown
    assert "BR-1, BR-2" in markdown
    assert "deadbeef" in markdown


def test_reports_are_written_and_json_is_source_of_truth(tmp_path: Path) -> None:
    data = _data()
    paths = generate_all_reports(data, tmp_path)
    json_path = tmp_path / "quality-metrics.json"
    markdown_path = tmp_path / "report.md"
    assert paths == {"json": str(json_path), "markdown": str(markdown_path)}
    assert json_path.is_file() and markdown_path.is_file()

    raw = json.loads(json_path.read_text(encoding="utf-8"))
    assert raw["pipeline_version"] == PIPELINE_VERSION
    assert raw["total_scenarios"] == 2
    assert raw["scenarios"][0]["case_summaries"][0]["id"] == "TC-01"
    assert raw["scenarios"][1]["quality"]["scenario_coverage"] is None
    assert raw["averages"]["scenario_coverage"] == 0.5

    restored = load_pipeline_data(tmp_path)
    assert restored.scenarios[0].cases[0].title == "Неверный email"
    assert restored.averages == raw["averages"]


def test_legacy_report_without_version_is_accepted(tmp_path: Path) -> None:
    generate_all_reports(_data(), tmp_path)
    path = tmp_path / "quality-metrics.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.pop("pipeline_version")
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    restored = load_pipeline_data(tmp_path)
    assert restored.scenarios[0].cases[0].id == "TC-01"


def test_report_version_mismatch_is_rejected(tmp_path: Path) -> None:
    generate_all_reports(_data(), tmp_path)
    path = tmp_path / "quality-metrics.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["pipeline_version"] = "9.9.9"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="несовместимая версия"):
        load_pipeline_data(tmp_path)


def test_missing_report_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_pipeline_data(tmp_path)
