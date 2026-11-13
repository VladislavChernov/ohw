"""Serialisation tests for the pipeline data models."""

import pytest

from src.config import DEFAULT_QUALITY_WEIGHTS
from src.generator.models import CaseType, TestCase
from src.models import PipelineData, QualityMetrics, ScenarioResult

CUSTOM_WEIGHTS = {
    "readability": 0.4,
    "clarity_goals": 0.2,
    "scenario_coverage": 0.2,
    "consistency": 0.2,
}


def _scenario() -> ScenarioResult:
    case = TestCase(
        id="TC-01",
        type=CaseType.POSITIVE,
        title="Успешный вход",
        steps=["Открыть форму", "Ввести данные"],
        expected="Открыт личный кабинет",
        requirement="BR-1",
    )
    return ScenarioResult(
        filename="requirements.md",
        source_path="input/requirements.md",
        artifact_type="testcases",
        output_file="output/scenarios/requirements.md",
        rendered="# Тест-кейсы\nBR-1",
        generations=["# Тест-кейсы\nBR-1", "# Тест-кейсы\nBR-1"],
        cases=[case],
        prompt="prompt",
        attempts=2,
        quality=QualityMetrics(
            readability=0.8,
            clarity_goals=0.6,
            scenario_coverage=1.0,
            consistency=0.9,
            overall=0.825,
        ),
        requirement_ids=["BR-1"],
        covered_requirements=["BR-1"],
        prompt_sha256="abc",
        runs=2,
    )


def test_snapshot_roundtrip() -> None:
    data = PipelineData(
        input_dir="input",
        artifact_type="testcases",
        model="qwen2.5:7b-instruct",
        runs=2,
        scenarios=[_scenario()],
        errors=["one error"],
    )
    snapshot = data.to_snapshot_dict()
    assert snapshot["pipeline_version"] == "0.1.0"
    restored = PipelineData.from_snapshot_dict(snapshot)
    assert restored.model == "qwen2.5:7b-instruct"
    assert restored.scenarios[0].cases[0].title == "Успешный вход"
    assert restored.scenarios[0].generations == _scenario().generations
    assert restored.averages["readability"] == 0.8
    assert restored.errors == ["one error"]


def test_report_roundtrip_keeps_real_cases() -> None:
    data = PipelineData(
        input_dir="input",
        artifact_type="testcases",
        model="qwen2.5:7b-instruct",
        scenarios=[_scenario()],
    )
    data.refresh_averages()
    report = data.to_report_dict()
    assert report["pipeline_version"] == "0.1.0"
    restored = PipelineData.from_report_dict(report)
    assert restored.scenarios[0].cases[0].id == "TC-01"
    assert restored.scenarios[0].covered_requirements == ["BR-1"]
    assert restored.averages == data.averages


def test_quality_weights_roundtrip() -> None:
    data = PipelineData(
        input_dir="input",
        quality_weights=CUSTOM_WEIGHTS,
        scenarios=[_scenario()],
    )
    snapshot = data.to_snapshot_dict()
    assert snapshot["quality_weights"] == CUSTOM_WEIGHTS
    assert PipelineData.from_snapshot_dict(snapshot).quality_weights == CUSTOM_WEIGHTS

    report = data.to_report_dict()
    assert report["quality_weights"] == CUSTOM_WEIGHTS
    assert PipelineData.from_report_dict(report).quality_weights == CUSTOM_WEIGHTS


def test_legacy_files_without_weights_use_defaults() -> None:
    data = PipelineData(input_dir="input", scenarios=[_scenario()])
    snapshot = data.to_snapshot_dict()
    snapshot.pop("quality_weights")
    assert PipelineData.from_snapshot_dict(snapshot).quality_weights == (
        DEFAULT_QUALITY_WEIGHTS
    )

    report = data.to_report_dict()
    report.pop("quality_weights")
    assert PipelineData.from_report_dict(report).quality_weights == (
        DEFAULT_QUALITY_WEIGHTS
    )


def test_invalid_weights_in_file_are_rejected() -> None:
    data = PipelineData(input_dir="input", scenarios=[_scenario()])
    snapshot = data.to_snapshot_dict()
    snapshot["quality_weights"] = {"readability": 1.0}
    with pytest.raises(ValueError, match="не заданы веса"):
        PipelineData.from_snapshot_dict(snapshot)


def test_successful_threshold_and_missing_metric() -> None:
    scenario = _scenario()
    scenario.quality = QualityMetrics(overall=0.5)
    assert scenario.successful is True
    scenario.quality = QualityMetrics(overall=None)
    assert scenario.successful is False
    data = PipelineData(scenarios=[scenario, _scenario()])
    assert data.successful == 1
    assert data.unsuccessful == 1


def test_refresh_averages_ignores_none() -> None:
    low = _scenario()
    low.quality = QualityMetrics(readability=1.0, scenario_coverage=None)
    high = _scenario()
    high.quality = QualityMetrics(readability=0.0, scenario_coverage=1.0)
    data = PipelineData(scenarios=[low, high])
    averages = data.refresh_averages()
    assert averages["readability"] == 0.5
    assert averages["scenario_coverage"] == 1.0
    assert averages["clarity_goals"] is None
