"""Pydantic schemas for the JSON artifacts passed between pipeline stages.

The pipeline reads two files written by earlier stages:

* ``generation.json`` — the full snapshot used by the assessment phase;
* ``quality-metrics.json`` — the report used by visualization and reporting.

Both files are validated before the dataclass layer touches them. Missing
optional fields keep the legacy defaults, while a missing required field or a
wrong type becomes a ``ValueError`` that names the file and the field, so the CLI
can return exit code ``2`` instead of a traceback.
"""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.generator.models import TestCase

SNAPSHOT_NAME = "generation.json"
REPORT_NAME = "quality-metrics.json"

# Pydantic error types that deserve a stable Russian message. Unknown types fall
# back to the original message so no diagnostic detail is lost.
_ERROR_MESSAGES: dict[str, str] = {
    "missing": "обязательное поле отсутствует",
    "string_type": "ожидается строка",
    "string_too_short": "строка не может быть пустой",
    "too_short": "элементов меньше допустимого",
    "too_long": "элементов больше допустимого",
    "int_type": "ожидается целое число",
    "int_parsing": "ожидается целое число",
    "int_from_float": "ожидается целое число",
    "float_type": "ожидается число",
    "float_parsing": "ожидается число",
    "finite_number": "ожидается конечное число",
    "bool_type": "ожидается логическое значение",
    "bool_parsing": "ожидается логическое значение",
    "list_type": "ожидается массив",
    "dict_type": "ожидается объект",
    "model_type": "ожидается объект",
    "enum": "недопустимое значение",
    "literal_error": "недопустимое значение",
}

_MAX_REPORTED_ERRORS = 3


class QualityMetricsPayload(BaseModel):
    """Optional metric values; ``None`` means the metric is not measurable."""

    model_config = ConfigDict(extra="ignore")

    readability: float | None = None
    clarity_goals: float | None = None
    scenario_coverage: float | None = None
    consistency: float | None = None
    overall: float | None = None


class ScenarioSnapshot(BaseModel):
    """One scenario inside ``generation.json``."""

    model_config = ConfigDict(extra="ignore")

    filename: str = Field(min_length=1)
    source_path: str = ""
    artifact_type: str = "testcases"
    output_file: str | None = None
    rendered: str = ""
    generations: list[str] | None = None
    cases: list[TestCase] | None = None
    prompt: str = ""
    attempts: int = 1
    quality: QualityMetricsPayload | None = None
    requirement_ids: list[str] | None = None
    covered_requirements: list[str] | None = None
    prompt_sha256: str = ""
    runs: int = 1
    error: str | None = None


class ScenarioReport(BaseModel):
    """One scenario inside ``quality-metrics.json``."""

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    filename: str = Field(min_length=1)
    artifact_type: str = "testcases"
    output_file: str | None = None
    quality: QualityMetricsPayload | None = None
    requirement_ids: list[str] | None = None
    cases_with_requirements: list[str] | None = None
    prompt_sha256: str = ""
    attempts: int = 1
    runs: int = 1
    case_summaries: list[TestCase] | None = None
    error: str | None = None


class SnapshotDocument(BaseModel):
    """Top-level structure of ``generation.json``."""

    model_config = ConfigDict(extra="ignore")

    pipeline_version: str | None = None
    input_dir: str = ""
    artifact_type: str = "testcases"
    model: str = ""
    runs: int = 1
    count: int = 10
    temperature: float = 0.7
    normalize: bool = False
    presets: list[str] | None = None
    generated_at: str = ""
    # Values are checked by ``validate_quality_weights`` to keep one source of
    # truth for completeness, sign and sum rules.
    quality_weights: dict[str, Any] | None = None
    scenarios: list[ScenarioSnapshot] | None = None
    errors: list[str] | None = None


class ReportDocument(BaseModel):
    """Top-level structure of ``quality-metrics.json``."""

    model_config = ConfigDict(extra="ignore")

    project: str | None = None
    version: str | None = None
    pipeline_version: str | None = None
    input_dir: str = ""
    artifact_type: str = "testcases"
    model: str = ""
    runs: int = 1
    count: int = 10
    temperature: float = 0.7
    normalize: bool = False
    presets: list[str] | None = None
    generated_at: str = ""
    quality_weights: dict[str, Any] | None = None
    total_scenarios: int | None = None
    successful: int | None = None
    unsuccessful: int | None = None
    quality_threshold: float | None = None
    averages: dict[str, float | None] | None = None
    charts: dict[str, str] | None = None
    errors: list[str] | None = None
    scenarios: list[ScenarioReport] | None = None


DocumentT = TypeVar("DocumentT", bound=BaseModel)


def _format_location(location: tuple[int | str, ...]) -> str:
    """Render a pydantic location as ``scenarios[0].filename``."""
    parts: list[str] = []
    for item in location:
        if isinstance(item, int):
            parts.append(f"[{item}]")
        else:
            if parts:
                parts.append(".")
            parts.append(str(item))
    return "".join(parts)


def _describe_error(error: Mapping[str, Any]) -> str:
    kind = error.get("type")
    if isinstance(kind, str) and kind in _ERROR_MESSAGES:
        return _ERROR_MESSAGES[kind]
    message = error.get("msg")
    return message if isinstance(message, str) else "неверное значение"


def _format_validation_error(exc: ValidationError, source: str) -> str:
    errors = exc.errors(include_url=False)
    details: list[str] = []
    for error in errors[:_MAX_REPORTED_ERRORS]:
        location = _format_location(error["loc"])
        details.append(f"{location or 'документ'}: {_describe_error(error)}")
    if len(errors) > _MAX_REPORTED_ERRORS:
        details.append(f"и ещё {len(errors) - _MAX_REPORTED_ERRORS}")
    return f"{source}: неверный формат артефакта — " + "; ".join(details)


def _validate(model: type[DocumentT], raw: Any, source: str) -> DocumentT:
    if not isinstance(raw, Mapping):
        raise ValueError(f"{source}: ожидается JSON-объект верхнего уровня")
    try:
        return model.model_validate(dict(raw))
    except ValidationError as exc:
        raise ValueError(_format_validation_error(exc, source)) from None


def read_json_object(path: Path) -> dict[str, Any]:
    """Read a UTF-8 JSON object, naming the file in parse and format errors."""
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"{path.name}: файл не является текстом UTF-8") from None
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{path.name}: некорректный JSON "
            f"(строка {exc.lineno}, колонка {exc.colno}): {exc.msg}"
        ) from None
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name}: ожидается JSON-объект верхнего уровня")
    return raw


def load_snapshot_document(
    raw: Any, source: str = SNAPSHOT_NAME
) -> SnapshotDocument:
    """Validate a parsed ``generation.json`` payload."""
    return _validate(SnapshotDocument, raw, source)


def load_report_document(
    raw: Any, source: str = REPORT_NAME
) -> ReportDocument:
    """Validate a parsed ``quality-metrics.json`` payload."""
    return _validate(ReportDocument, raw, source)


def load_scenario_snapshot(
    raw: Any, source: str = SNAPSHOT_NAME
) -> ScenarioSnapshot:
    """Validate a single scenario payload for direct model callers."""
    return _validate(ScenarioSnapshot, raw, source)


def load_scenario_report(
    raw: Any, source: str = REPORT_NAME
) -> ScenarioReport:
    """Validate a single report scenario payload for direct model callers."""
    return _validate(ScenarioReport, raw, source)
