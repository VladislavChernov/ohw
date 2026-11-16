"""Tests for the pydantic artifact contracts."""

import json
from pathlib import Path

import pytest

from src.config import PIPELINE_VERSION
from src.contracts import (
    REPORT_NAME,
    SNAPSHOT_NAME,
    read_json_object,
)
from src.models import PipelineData
from src.reporting.report_generator import load_pipeline_data
from src.workflow.stages import load_snapshot

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "live-run"


def _snapshot_dict() -> dict:
    return {
        "pipeline_version": PIPELINE_VERSION,
        "input_dir": "input",
        "artifact_type": "testcases",
        "model": "qwen2.5:7b-instruct",
        "runs": 1,
        "count": 1,
        "temperature": 0.2,
        "normalize": False,
        "presets": [],
        "generated_at": "2026-11-15T10:00:00+00:00",
        "scenarios": [
            {
                "filename": "requirements.md",
                "rendered": "BR-1. Система должна проверять форму.",
                "cases": [
                    {
                        "id": "TC-01",
                        "type": "positive",
                        "title": "Успешный вход",
                        "steps": ["Открыть форму"],
                        "expected": "Форма открыта",
                    }
                ],
            }
        ],
        "errors": [],
    }


def _report_dict() -> dict:
    return {
        "project": "graduation-project",
        "version": "0.3.0",
        "pipeline_version": PIPELINE_VERSION,
        "input_dir": "input",
        "artifact_type": "testcases",
        "model": "qwen2.5:7b-instruct",
        "runs": 1,
        "count": 1,
        "temperature": 0.2,
        "normalize": False,
        "presets": [],
        "generated_at": "2026-11-15T10:00:00+00:00",
        "total_scenarios": 1,
        "successful": 1,
        "unsuccessful": 0,
        "quality_threshold": 0.5,
        "averages": {"overall": 0.8},
        "charts": {},
        "errors": [],
        "scenarios": [
            {
                "id": 1,
                "filename": "requirements.md",
                "artifact_type": "testcases",
                "quality": {"overall": 0.8},
                "case_summaries": [],
            }
        ],
    }


def test_valid_snapshot_is_accepted() -> None:
    restored = PipelineData.from_snapshot_dict(_snapshot_dict())
    assert restored.scenarios[0].filename == "requirements.md"
    assert restored.scenarios[0].cases[0].id == "TC-01"


def test_valid_report_is_accepted() -> None:
    restored = PipelineData.from_report_dict(_report_dict())
    assert restored.scenarios[0].filename == "requirements.md"
    assert restored.averages["overall"] == 0.8


def test_extra_fields_are_ignored() -> None:
    raw = _snapshot_dict()
    raw["future_flag"] = True
    raw["scenarios"][0]["future_field"] = "ignored"
    restored = PipelineData.from_snapshot_dict(raw)
    assert restored.scenarios[0].filename == "requirements.md"


def test_snapshot_without_filename_names_file_and_field() -> None:
    raw = _snapshot_dict()
    raw["scenarios"][0].pop("filename")
    with pytest.raises(ValueError) as exc:
        PipelineData.from_snapshot_dict(raw, source=SNAPSHOT_NAME)
    message = str(exc.value)
    assert message.startswith(f"{SNAPSHOT_NAME}: неверный формат артефакта")
    assert "scenarios[0].filename" in message
    assert "обязательное поле отсутствует" in message


def test_report_without_filename_names_file_and_field() -> None:
    raw = _report_dict()
    raw["scenarios"][0].pop("filename")
    with pytest.raises(ValueError) as exc:
        PipelineData.from_report_dict(raw, source=REPORT_NAME)
    message = str(exc.value)
    assert message.startswith(f"{REPORT_NAME}: неверный формат артефакта")
    assert "scenarios[0].filename" in message


def test_wrong_type_is_rejected_with_path() -> None:
    raw = _snapshot_dict()
    raw["scenarios"][0]["attempts"] = "много"
    with pytest.raises(ValueError) as exc:
        PipelineData.from_snapshot_dict(raw)
    message = str(exc.value)
    assert "scenarios[0].attempts" in message
    assert "ожидается целое число" in message


def test_fractional_integer_is_rejected_in_russian() -> None:
    raw = _snapshot_dict()
    raw["scenarios"][0]["attempts"] = 2.5
    with pytest.raises(ValueError) as exc:
        PipelineData.from_snapshot_dict(raw)
    message = str(exc.value)
    assert "scenarios[0].attempts" in message
    assert "ожидается целое число" in message


def test_empty_steps_are_rejected_in_russian() -> None:
    raw = _snapshot_dict()
    raw["scenarios"][0]["cases"][0]["steps"] = []
    with pytest.raises(ValueError) as exc:
        PipelineData.from_snapshot_dict(raw)
    message = str(exc.value)
    assert "scenarios[0].cases[0].steps" in message
    assert "элементов меньше допустимого" in message


def test_weights_error_names_the_file() -> None:
    raw = _snapshot_dict()
    raw["quality_weights"] = {"readability": 1.0}
    with pytest.raises(ValueError) as exc:
        PipelineData.from_snapshot_dict(raw)
    message = str(exc.value)
    assert message.startswith(f"{SNAPSHOT_NAME}: ")
    assert "не заданы веса" in message


def test_scenarios_must_be_a_list() -> None:
    raw = _snapshot_dict()
    raw["scenarios"] = {}
    with pytest.raises(ValueError, match="ожидается массив"):
        PipelineData.from_snapshot_dict(raw)


def test_invalid_case_is_rejected_with_nested_path() -> None:
    raw = _snapshot_dict()
    raw["scenarios"][0]["cases"][0].pop("expected")
    with pytest.raises(ValueError) as exc:
        PipelineData.from_snapshot_dict(raw)
    assert "scenarios[0].cases[0].expected" in str(exc.value)


def test_null_collections_keep_legacy_behaviour() -> None:
    raw = _snapshot_dict()
    raw["scenarios"][0]["generations"] = None
    raw["scenarios"][0]["cases"] = None
    raw["presets"] = None
    restored = PipelineData.from_snapshot_dict(raw)
    assert restored.scenarios[0].generations == []
    assert restored.scenarios[0].cases == []
    assert restored.presets == []


def test_legacy_snapshot_without_version_and_weights() -> None:
    raw = _snapshot_dict()
    raw.pop("pipeline_version")
    raw.pop("quality_weights", None)
    restored = PipelineData.from_snapshot_dict(raw)
    assert restored.scenarios[0].filename == "requirements.md"


def test_read_json_object_reports_broken_json(tmp_path: Path) -> None:
    path = tmp_path / SNAPSHOT_NAME
    path.write_text('{"scenarios": [', encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        read_json_object(path)
    message = str(exc.value)
    assert SNAPSHOT_NAME in message
    assert "некорректный JSON" in message


def test_read_json_object_rejects_non_object(tmp_path: Path) -> None:
    path = tmp_path / REPORT_NAME
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON-объект верхнего уровня"):
        read_json_object(path)


def test_read_json_object_names_non_utf8_file(tmp_path: Path) -> None:
    path = tmp_path / SNAPSHOT_NAME
    path.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(ValueError, match="UTF-8"):
        read_json_object(path)


def test_bundled_legacy_examples_are_readable() -> None:
    snapshot = load_snapshot(EXAMPLES)
    report = load_pipeline_data(EXAMPLES)
    assert snapshot.scenarios
    assert report.scenarios
    assert snapshot.scenarios[0].filename == report.scenarios[0].filename


def test_loaders_survive_a_roundtrip(tmp_path: Path) -> None:
    (tmp_path / SNAPSHOT_NAME).write_text(
        json.dumps(_snapshot_dict(), ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / REPORT_NAME).write_text(
        json.dumps(_report_dict(), ensure_ascii=False), encoding="utf-8"
    )
    assert load_snapshot(tmp_path).scenarios[0].filename == "requirements.md"
    assert load_pipeline_data(tmp_path).scenarios[0].filename == "requirements.md"
