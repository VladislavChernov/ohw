"""Degradation statistics and baseline/candidate comparison."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from llm_degradation.config import Thresholds
from llm_degradation.degradation import (
    DEGRADATION_MIN_RUNS,
    DegradationError,
    bootstrap_ci,
    cohens_h,
    compare_summaries,
    run_degradation,
    wilson_interval,
)
from llm_degradation.models import (
    EnvironmentInfo,
    ExperimentReport,
    NormalizedInput,
    PromptBundle,
    PromptInfo,
)
from llm_degradation.runner import SeriesOptions


def summary(
    *,
    pass_at_1: float = 1.0,
    invented_br_total: int = 0,
    br_coverage_mean: float = 1.0,
    valid_json_rate: float = 1.0,
    mean_attempts: float = 1.0,
    runs: int = 5,
) -> dict[str, Any]:
    return {
        "runs": runs,
        "pass_at_1": pass_at_1,
        "invented_br_total": invented_br_total,
        "br_coverage_mean": br_coverage_mean,
        "valid_json_rate": valid_json_rate,
        "mean_attempts": mean_attempts,
    }


def test_wilson_interval_edge_cases() -> None:
    assert wilson_interval(0, 0) == (0.0, 1.0)
    low, high = wilson_interval(0, 10)
    assert low == 0.0
    assert 0.0 < high < 0.4
    low, high = wilson_interval(10, 10)
    assert 0.6 < low < 1.0
    assert high == 1.0


def test_wilson_interval_contains_the_point_estimate() -> None:
    low, high = wilson_interval(5, 10)
    assert low < 0.5 < high


def test_bootstrap_ci_edge_cases() -> None:
    assert bootstrap_ci([]) == (0.0, 0.0)
    assert bootstrap_ci([0.4]) == (0.4, 0.4)


def test_bootstrap_ci_is_deterministic() -> None:
    values = [0.0, 1.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0]
    first = bootstrap_ci(values)
    second = bootstrap_ci(values)
    assert first == second
    assert 0.0 <= first[0] <= first[1] <= 1.0


def test_cohens_h() -> None:
    assert cohens_h(0.5, 0.5) == 0.0
    assert cohens_h(0.2, 0.8) > 0.0
    assert cohens_h(0.8, 0.2) < 0.0


def test_insufficient_data_below_minimum_runs() -> None:
    comparison, verdict = compare_summaries(
        summary(),
        summary(),
        thresholds=Thresholds(),
        baseline_runs=2,
        candidate_runs=2,
    )
    assert verdict.status == "insufficient_data"
    assert comparison["pass_at_1"]["diff"] == 0.0
    assert DEGRADATION_MIN_RUNS == 5


def test_unchanged_cells() -> None:
    _, verdict = compare_summaries(
        summary(),
        summary(),
        thresholds=Thresholds(),
        baseline_runs=5,
        candidate_runs=5,
    )
    assert verdict.status == "unchanged"
    assert verdict.reasons == []


def test_pass_drop_with_non_overlapping_intervals_regresses() -> None:
    comparison, verdict = compare_summaries(
        summary(pass_at_1=1.0),
        summary(pass_at_1=0.4),
        thresholds=Thresholds(),
        baseline_runs=20,
        candidate_runs=20,
    )
    assert verdict.status == "regressed"
    assert any("pass@1 dropped" in reason for reason in verdict.reasons)
    assert comparison["pass_at_1"]["overlap"] is False
    assert comparison["effect_size_h"] < 0


def test_pass_drop_with_overlapping_intervals_is_not_a_regression() -> None:
    _, verdict = compare_summaries(
        summary(pass_at_1=0.8),
        summary(pass_at_1=0.6),
        thresholds=Thresholds(),
        baseline_runs=5,
        candidate_runs=5,
    )
    assert verdict.status == "unchanged"


def test_br_coverage_drop_regresses() -> None:
    _, verdict = compare_summaries(
        summary(br_coverage_mean=1.0),
        summary(br_coverage_mean=0.8),
        thresholds=Thresholds(),
        baseline_runs=5,
        candidate_runs=5,
    )
    assert verdict.status == "regressed"
    assert any("br_coverage_mean dropped" in reason for reason in verdict.reasons)


def test_json_rate_drop_regresses() -> None:
    _, verdict = compare_summaries(
        summary(valid_json_rate=1.0),
        summary(valid_json_rate=0.7),
        thresholds=Thresholds(),
        baseline_runs=5,
        candidate_runs=5,
    )
    assert verdict.status == "regressed"
    assert any("valid_json_rate dropped" in reason for reason in verdict.reasons)


def test_attempts_increase_regresses() -> None:
    _, verdict = compare_summaries(
        summary(mean_attempts=1.0),
        summary(mean_attempts=2.5),
        thresholds=Thresholds(),
        baseline_runs=5,
        candidate_runs=5,
    )
    assert verdict.status == "regressed"
    assert any("mean_attempts increased" in reason for reason in verdict.reasons)


def test_invented_br_increase_regresses() -> None:
    _, verdict = compare_summaries(
        summary(invented_br_total=0),
        summary(invented_br_total=3),
        thresholds=Thresholds(),
        baseline_runs=5,
        candidate_runs=5,
    )
    assert verdict.status == "regressed"
    assert any("invented_br_total increased" in reason for reason in verdict.reasons)


def test_improvements_are_reported() -> None:
    _, verdict = compare_summaries(
        summary(pass_at_1=0.2, invented_br_total=5, br_coverage_mean=0.5, valid_json_rate=0.5),
        summary(pass_at_1=0.9, invented_br_total=0, br_coverage_mean=1.0, valid_json_rate=1.0),
        thresholds=Thresholds(),
        baseline_runs=20,
        candidate_runs=20,
    )
    assert verdict.status == "improved"
    assert len(verdict.reasons) >= 3


def test_bootstrap_block_is_added_when_values_are_present() -> None:
    comparison, _ = compare_summaries(
        summary(),
        summary(),
        thresholds=Thresholds(),
        baseline_runs=5,
        candidate_runs=5,
        baseline_values=[1.0, 1.0, 1.0, 1.0, 1.0],
        candidate_values=[1.0, 1.0, 0.0, 1.0, 1.0],
    )
    assert comparison["bootstrap_pass_at_1"]["baseline"] == [1.0, 1.0]


class FakeLabClient:
    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)
        self.calls = 0
        self.models: list[str | None] = []

    def chat(
        self,
        system: str,
        user: str,
        temperature: float,
        *,
        model: str | None = None,
        **_: Any,
    ) -> str:
        self.calls += 1
        self.models.append(model)
        if not self.answers:
            raise AssertionError("unexpected chat call")
        return self.answers.pop(0)


def payload() -> str:
    cases = [
        {
            "id": f"TC-0{index + 1}",
            "type": "negative" if index >= 2 else "positive",
            "title": "Sample",
            "steps": ["step"],
            "expected": "result",
            "requirement": f"BR-{min(index + 1, 4)}",
        }
        for index in range(5)
    ]
    return json.dumps({"cases": cases})


def make_baseline_report(prompt: PromptBundle, *, runs: int = 5) -> ExperimentReport:
    return ExperimentReport(
        run_id="baseline-1",
        kind="consistency",
        started_at="2026-01-01T00:00:00Z",
        finished_at="2026-01-01T00:01:00Z",
        environment=EnvironmentInfo(model="llama3", temperature=0.7, seed=42),
        prompt=PromptInfo(
            artifact_type=prompt.artifact_type.value,
            count=prompt.count,
            presets=list(prompt.presets),
            prompt_sha256=prompt.prompt_sha256,
        ),
        params={"runs": runs},
        summary=summary(runs=runs),
    )


def test_run_degradation_runs_both_cells(
    normalized: NormalizedInput,
    prompt: PromptBundle,
    prompt_dir: Path,
) -> None:
    answers = [payload() for _ in range(DEGRADATION_MIN_RUNS * 2)]
    client = FakeLabClient(answers)

    result = run_degradation(
        client,  # type: ignore[arg-type]
        normalized,
        prompt,
        baseline_model="llama3",
        candidate_model="llama3:q4",
        options=SeriesOptions(runs=DEGRADATION_MIN_RUNS, temperature=0.7),
        thresholds=Thresholds(),
        prompt_dir=prompt_dir,
    )

    assert client.calls == DEGRADATION_MIN_RUNS * 2
    assert client.models[:DEGRADATION_MIN_RUNS] == ["llama3"] * DEGRADATION_MIN_RUNS
    assert client.models[DEGRADATION_MIN_RUNS:] == ["llama3:q4"] * DEGRADATION_MIN_RUNS
    assert result.baseline_model == "llama3"
    assert result.candidate_model == "llama3:q4"
    assert result.verdict.status == "unchanged"
    assert result.baseline.runs == 0
    assert result.baseline_summary["runs"] == DEGRADATION_MIN_RUNS
    assert result.candidate.runs == DEGRADATION_MIN_RUNS
    assert {run.cell for run in result.runs} == {"baseline", "candidate"}


def test_run_degradation_uses_a_baseline_report(
    normalized: NormalizedInput,
    prompt: PromptBundle,
    prompt_dir: Path,
) -> None:
    report = make_baseline_report(prompt)
    client = FakeLabClient([payload() for _ in range(DEGRADATION_MIN_RUNS)])

    result = run_degradation(
        client,  # type: ignore[arg-type]
        normalized,
        prompt,
        baseline_model=None,
        candidate_model="llama3:q4",
        options=SeriesOptions(runs=DEGRADATION_MIN_RUNS),
        thresholds=Thresholds(),
        prompt_dir=prompt_dir,
        baseline_report=report,
    )

    assert client.calls == DEGRADATION_MIN_RUNS
    assert result.baseline_model == "llama3"
    assert result.baseline.runs == 0


def test_baseline_report_prompt_mismatch_is_rejected(
    normalized: NormalizedInput,
    prompt: PromptBundle,
) -> None:
    report = make_baseline_report(prompt).model_copy(
        update={"prompt": PromptInfo(artifact_type="testcases", count=5, prompt_sha256="other")}
    )
    with pytest.raises(DegradationError, match="prompt_sha256"):
        run_degradation(
            FakeLabClient([]),  # type: ignore[arg-type]
            normalized,
            prompt,
            baseline_model=None,
            candidate_model="llama3",
            options=SeriesOptions(runs=1),
            thresholds=Thresholds(),
            baseline_report=report,
        )


def test_baseline_source_must_be_exactly_one(
    normalized: NormalizedInput,
    prompt: PromptBundle,
) -> None:
    with pytest.raises(DegradationError, match="exactly one baseline source"):
        run_degradation(
            FakeLabClient([]),  # type: ignore[arg-type]
            normalized,
            prompt,
            baseline_model="llama3",
            candidate_model="llama3",
            options=SeriesOptions(runs=1),
            thresholds=Thresholds(),
            baseline_report=make_baseline_report(prompt),
        )
