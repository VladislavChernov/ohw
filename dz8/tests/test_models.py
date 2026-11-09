"""Contract tests for report dataclasses and pydantic models.

These tests are intentionally narrow: they pin the public shape of the models
that end up in ``report.json`` and make sure extra fields cannot sneak in.
"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from ai_testgen.models import ArtifactType
from ai_testgen.normalizer import NormalizerSettings
from pydantic import ValidationError

from llm_degradation.models import (
    SCHEMA_VERSION,
    ArtifactMetrics,
    CanaryResult,
    CellReport,
    CellSummary,
    ChatCall,
    EnvironmentInfo,
    ErrorInfo,
    ErrorReport,
    ExperimentReport,
    InputInfo,
    NormalizedInput,
    ProbeResult,
    PromptBundle,
    PromptInfo,
    RunParams,
    RunRecord,
    VerdictInfo,
)


def make_report(**updates: object) -> ExperimentReport:
    data: dict[str, object] = {
        "run_id": "run-1",
        "kind": "consistency",
        "started_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-01T00:01:00Z",
    }
    data.update(updates)
    return ExperimentReport(**data)  # type: ignore[arg-type]


def make_normalized() -> NormalizedInput:
    return NormalizedInput(
        source=Path("input/catalog.md"),
        source_sha256="source-hash",
        encoding="utf-8",
        text="# Title\n\n### BR-1\nBody",
        text_sha256="text-hash",
        title="Title",
        url="https://example.com",
        br_ids=("BR-1",),
        settings=NormalizerSettings(),
        warnings=(),
    )


def test_schema_version_is_one() -> None:
    assert SCHEMA_VERSION == 1
    assert make_report().schema_version == 1


def test_normalized_input_is_frozen() -> None:
    normalized = make_normalized()
    assert normalized.br_ids == ("BR-1",)
    with pytest.raises(FrozenInstanceError):
        normalized.title = "other"  # type: ignore[misc]


def test_normalized_input_allows_missing_url_and_warnings() -> None:
    normalized = NormalizedInput(
        source=Path("input/no-url.md"),
        source_sha256="source",
        encoding="cp1251",
        text="text",
        text_sha256="hash",
        title="",
        url=None,
        br_ids=(),
        settings=NormalizerSettings(id_prefix="REQ"),
        warnings=("no URL found",),
    )
    assert normalized.url is None
    assert normalized.settings.id_prefix == "REQ"
    assert normalized.warnings == ("no URL found",)


def test_prompt_bundle_is_frozen() -> None:
    bundle = PromptBundle(
        artifact_type=ArtifactType.TESTCASES,
        system="system",
        user="user",
        prompt_sha256="hash",
        count=5,
        presets=("negative-scenarios",),
    )
    assert bundle.artifact_type is ArtifactType.TESTCASES
    with pytest.raises(FrozenInstanceError):
        bundle.count = 6  # type: ignore[misc]


def test_chat_call_defaults() -> None:
    call = ChatCall()
    assert call.index == 1
    assert call.system == ""
    assert call.user == ""
    assert call.json_mode is False
    assert call.raw == ""
    assert call.error is None


def test_chat_call_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        ChatCall(unknown=True)  # type: ignore[call-arg]


def test_chat_call_is_frozen() -> None:
    call = ChatCall(index=2)
    with pytest.raises(ValidationError):
        call.index = 3  # type: ignore[misc]


def test_artifact_metrics_defaults() -> None:
    metrics = ArtifactMetrics()
    assert metrics.validator_pass is False
    assert metrics.cases_total == 0
    assert metrics.br_coverage == 0.0
    assert metrics.attempts == 1


def test_artifact_metrics_has_independent_lists() -> None:
    first = ArtifactMetrics()
    second = ArtifactMetrics()
    first.tc_ids.append("TC-01")
    assert second.tc_ids == []
    assert first.br_covered == []


def test_artifact_metrics_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        ArtifactMetrics(unknown=True)  # type: ignore[call-arg]


def test_run_record_requires_index() -> None:
    with pytest.raises(ValidationError):
        RunRecord()  # type: ignore[call-arg]
    record = RunRecord(index=1)
    assert record.metrics == ArtifactMetrics()
    assert record.cell is None
    assert record.error is None


def test_run_record_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        RunRecord(index=1, unknown=True)  # type: ignore[call-arg]


def test_probe_result_defaults() -> None:
    result = ProbeResult(id="H-01", kind="count", status="pass")
    assert result.critical is False
    assert result.forbidden_hits == []
    assert result.details == []
    assert result.failed_runs == 0


def test_canary_result_defaults() -> None:
    result = CanaryResult()
    assert result.verdict == "fail"
    assert result.parse_error is None
    assert result.ids_exact is False
    assert result.raw == ""


def test_cell_summary_requires_model() -> None:
    with pytest.raises(ValidationError):
        CellSummary()  # type: ignore[call-arg]
    summary = CellSummary(model="llama3")
    assert summary.params == RunParams()
    assert summary.tc_jaccard_mean is None
    assert summary.unique_artifacts == 0


def test_environment_info_defaults() -> None:
    environment = EnvironmentInfo()
    assert environment.model == ""
    assert environment.digest is None
    assert environment.temperature == 0.7
    assert environment.seed == 42
    assert environment.max_retries == 3


def test_input_info_has_independent_lists_and_dicts() -> None:
    first = InputInfo()
    second = InputInfo()
    first.br_ids.append("BR-1")
    first.normalizer["id_prefix"] = "BR"
    assert second.br_ids == []
    assert second.normalizer == {}


def test_prompt_info_defaults() -> None:
    info = PromptInfo()
    assert info.artifact_type == "testcases"
    assert info.count == 5
    assert info.presets == []
    assert info.prompt_sha256 == ""


def test_verdict_info_defaults() -> None:
    verdict = VerdictInfo()
    assert verdict.status == "fail"
    assert verdict.reasons == []
    assert verdict.thresholds == {}


def test_cell_report_requires_model() -> None:
    with pytest.raises(ValidationError):
        CellReport()  # type: ignore[call-arg]
    report = CellReport(model="llama3")
    assert report.params == RunParams()
    assert report.summary == {}


def test_experiment_report_minimal() -> None:
    report = make_report()
    assert report.environment == EnvironmentInfo()
    assert report.input == InputInfo()
    assert report.prompt == PromptInfo()
    assert report.runs == []
    assert report.verdict == VerdictInfo()
    assert report.baseline is None
    assert report.candidate is None
    assert report.comparison is None


def test_experiment_report_has_independent_defaults() -> None:
    first = make_report()
    second = make_report()
    first.runs.append(RunRecord(index=1))
    first.warnings.append("warning")
    assert second.runs == []
    assert second.warnings == []


def test_experiment_report_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        make_report(unknown=True)


def test_experiment_report_is_frozen() -> None:
    report = make_report()
    with pytest.raises(ValidationError):
        report.kind = "other"  # type: ignore[misc]


def test_experiment_report_json_round_trip() -> None:
    report = make_report(
        runs=[RunRecord(index=1, metrics=ArtifactMetrics(cases_total=5))],
        verdict=VerdictInfo(status="pass", reasons=[], thresholds={"x": 1.0}),
    )
    payload = json.loads(report.model_dump_json())
    restored = ExperimentReport.model_validate(payload)
    assert restored == report
    assert restored.runs[0].metrics.cases_total == 5


def test_error_models() -> None:
    error = ErrorInfo(code=2, message="bad input")
    assert error.hint is None
    report = ErrorReport(error=error)
    assert report.ok is False
    assert report.kind == "error"
    assert report.schema_version == SCHEMA_VERSION


def test_run_params_defaults_and_frozen() -> None:
    params = RunParams()
    assert params.temperature == 0.7
    assert params.seed == 42
    assert params.num_ctx == 4096
    assert params.num_predict == 512
    assert params.max_retries == 3
    with pytest.raises(ValidationError):
        params.temperature = 0.0  # type: ignore[misc]
