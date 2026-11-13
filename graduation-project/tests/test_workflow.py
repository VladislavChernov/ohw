"""Tests for stage selection and the file-based workflow."""

import json
from pathlib import Path

import pytest

from src.config import PIPELINE_VERSION
from src.generator.models import CaseType, TestCase
from src.generator.ollama_client import OllamaClient, OllamaConnectionError
from src.models import PipelineData, QualityMetrics, ScenarioResult
from src.workflow.main import (
    EXIT_GENERATION,
    EXIT_USAGE,
    build_parser,
    main,
    selected_phases,
)


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


def test_snapshot_contains_pipeline_version(tmp_path: Path) -> None:
    _snapshot(tmp_path)
    raw = json.loads(
        (tmp_path / "generation.json").read_text(encoding="utf-8")
    )
    assert raw["pipeline_version"] == PIPELINE_VERSION


def test_legacy_snapshot_without_version_is_accepted(tmp_path: Path) -> None:
    _snapshot(tmp_path)
    path = tmp_path / "generation.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.pop("pipeline_version")
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert main(["--assess", "--output-dir", str(tmp_path)]) == 0


def test_snapshot_version_mismatch_returns_usage_error(tmp_path: Path) -> None:
    _snapshot(tmp_path)
    path = tmp_path / "generation.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["pipeline_version"] = "9.9.9"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert main(["--assess", "--output-dir", str(tmp_path)]) == EXIT_USAGE


def _write_inputs(tmp_path: Path) -> Path:
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    (input_dir / "first.md").write_text(
        "BR-1. Проверка первого файла. Система должна работать.\n",
        encoding="utf-8",
    )
    (input_dir / "second.md").write_text(
        "BR-2. Проверка второго файла. Система должна работать.\n",
        encoding="utf-8",
    )
    return input_dir


def _patch_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        OllamaClient,
        "list_models",
        lambda self: ["qwen2.5:7b-instruct"],
    )

    def chat(
        self: OllamaClient,
        system: str,
        user: str,
        temperature: float,
        json_mode: bool = True,
    ) -> str:
        if "первого файла" in user:
            raise OllamaConnectionError("ollama service is unreachable")
        return json.dumps(
            {
                "cases": [
                    {
                        "id": "TC-01",
                        "type": "positive",
                        "title": "Успех",
                        "steps": ["Открыть форму"],
                        "expected": "Форма открыта",
                        "requirement": "BR-2",
                    },
                    {
                        "id": "TC-02",
                        "type": "negative",
                        "title": "Ошибка",
                        "steps": ["Открыть форму"],
                        "expected": "Показана ошибка",
                        "requirement": "BR-2",
                    },
                ]
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(OllamaClient, "chat", chat)


def test_continue_on_error_processes_remaining_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_dir = _write_inputs(tmp_path)
    _patch_ollama(monkeypatch)
    output_dir = tmp_path / "output"

    code = main(
        [
            "--generate",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--count",
            "2",
            "--continue-on-error",
        ]
    )

    assert code == 0
    snapshot = json.loads(
        (output_dir / "generation.json").read_text(encoding="utf-8")
    )
    assert snapshot["pipeline_version"] == PIPELINE_VERSION
    assert [item["filename"] for item in snapshot["scenarios"]] == ["second.md"]
    assert len(snapshot["errors"]) == 1
    assert "first.md" in snapshot["errors"][0]


def test_without_continue_on_error_generation_aborts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_dir = _write_inputs(tmp_path)
    _patch_ollama(monkeypatch)

    code = main(
        [
            "--generate",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(tmp_path / "output"),
            "--count",
            "2",
        ]
    )

    assert code == EXIT_GENERATION


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


def test_snapshot_contains_default_quality_weights(tmp_path: Path) -> None:
    _snapshot(tmp_path)
    raw = json.loads(
        (tmp_path / "generation.json").read_text(encoding="utf-8")
    )
    assert raw["quality_weights"] == {
        "readability": 0.25,
        "clarity_goals": 0.25,
        "scenario_coverage": 0.30,
        "consistency": 0.20,
    }


def test_assess_reuses_weights_from_snapshot(tmp_path: Path) -> None:
    _snapshot(tmp_path)
    path = tmp_path / "generation.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["quality_weights"] = {
        "readability": 1.0,
        "clarity_goals": 0.0,
        "scenario_coverage": 0.0,
        "consistency": 0.0,
    }
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert main(["--assess", "--output-dir", str(tmp_path)]) == 0
    report = json.loads(
        (tmp_path / "quality-metrics.json").read_text(encoding="utf-8")
    )
    quality = report["scenarios"][0]["quality"]
    assert quality["overall"] == quality["readability"]


def test_report_shows_quality_weights(tmp_path: Path) -> None:
    _snapshot(tmp_path)
    assert main(["--assess", "--report", "--output-dir", str(tmp_path)]) == 0
    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "| Веса overall | readability=0.25, clarity_goals=0.25, " in markdown


@pytest.mark.parametrize(
    "weights",
    [
        "readability=1.0",
        "readability=0.5,readability=0.5,clarity_goals=0,"
        "scenario_coverage=0,consistency=0",
        "readability=0.5,clarity_goals=0.6,scenario_coverage=0,"
        "consistency=0",
    ],
)
def test_invalid_quality_weights_return_usage_error(weights: str) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--quality-weights", weights])
    assert exc.value.code == EXIT_USAGE


def test_generate_stores_cli_quality_weights(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_dir = _write_inputs(tmp_path)
    _patch_ollama(monkeypatch)
    output_dir = tmp_path / "output"
    weights = {
        "readability": 0.4,
        "clarity_goals": 0.2,
        "scenario_coverage": 0.2,
        "consistency": 0.2,
    }

    code = main(
        [
            "--generate",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
            "--count",
            "2",
            "--continue-on-error",
            "--quality-weights",
            "readability=0.4,clarity_goals=0.2,"
            "scenario_coverage=0.2,consistency=0.2",
        ]
    )

    assert code == 0
    snapshot = json.loads(
        (output_dir / "generation.json").read_text(encoding="utf-8")
    )
    assert snapshot["quality_weights"] == weights
