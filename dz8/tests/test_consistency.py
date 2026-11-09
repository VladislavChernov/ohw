"""Consistency summary and verdicts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ai_testgen.models import ArtifactType

from llm_degradation.config import Thresholds
from llm_degradation.models import (
    ArtifactMetrics,
    NormalizedInput,
    PromptBundle,
    RunRecord,
)
from llm_degradation.probes.consistency import (
    cell_summary,
    run_consistency,
    summarize_series,
)
from llm_degradation.runner import SeriesOptions


def make_run(
    index: int,
    *,
    tc_ids: tuple[str, ...] = ("TC-01", "TC-02", "TC-03"),
    headings: tuple[str, ...] = ("Scope", "Risks"),
    title_tokens: tuple[str, ...] = ("plan",),
    validator_pass: bool = True,
    attempts: int = 1,
    br_coverage: float = 1.0,
    count_matches: bool = True,
    json_valid: bool = True,
    positive: int = 2,
    negative: int = 3,
    cases_total: int = 5,
    raw_sha256: str | None = None,
) -> RunRecord:
    metrics = ArtifactMetrics(
        json_valid=json_valid,
        count_matches=count_matches,
        validator_pass=validator_pass,
        cases_total=cases_total,
        positive=positive,
        negative=negative,
        br_coverage=br_coverage,
        tc_ids=list(tc_ids),
        section_headings=list(headings),
        title_tokens=list(title_tokens),
    )
    return RunRecord(
        index=index,
        attempts=attempts,
        latency_ms=100.0,
        raw_sha256=raw_sha256 or f"hash-{index}",
        validator_pass=validator_pass,
        metrics=metrics,
    )


def test_empty_series_fails() -> None:
    summary, verdict = summarize_series(
        [],
        artifact_type=ArtifactType.TESTCASES,
        expected_count=5,
        temperature=0.7,
        thresholds=Thresholds(),
    )
    assert summary["runs"] == 0
    assert verdict.status == "fail"
    assert verdict.reasons == ["no runs recorded"]


def test_identical_valid_runs_pass() -> None:
    runs = [make_run(1, raw_sha256="same"), make_run(2, raw_sha256="same")]
    summary, verdict = summarize_series(
        runs,
        artifact_type=ArtifactType.TESTCASES,
        expected_count=5,
        temperature=0.7,
        thresholds=Thresholds(),
    )
    assert verdict.status == "pass"
    assert summary["pass_at_1"] == 1.0
    assert summary["pass_all"] == 1.0
    assert summary["valid_json_rate"] == 1.0
    assert summary["count_match_rate"] == 1.0
    assert summary["tc_jaccard_mean"] == 1.0
    assert summary["heading_jaccard_mean"] == 1.0
    assert summary["identical_runs"] == 1
    assert summary["unique_artifacts"] == 1
    assert summary["self_consistency"] == 1.0


def test_low_validator_pass_rate_fails() -> None:
    runs = [
        make_run(1),
        make_run(2, validator_pass=False),
        make_run(3, validator_pass=False),
    ]
    _, verdict = summarize_series(
        runs,
        artifact_type=ArtifactType.TESTCASES,
        expected_count=5,
        temperature=0.7,
        thresholds=Thresholds(validator_pass_rate_min=0.9),
    )
    assert verdict.status == "fail"
    assert any("validator_pass_rate" in reason for reason in verdict.reasons)


def test_count_mismatch_fails_for_testcases() -> None:
    runs = [make_run(1, count_matches=False), make_run(2, count_matches=False)]
    _, verdict = summarize_series(
        runs,
        artifact_type=ArtifactType.TESTCASES,
        expected_count=5,
        temperature=0.7,
        thresholds=Thresholds(),
    )
    assert verdict.status == "fail"
    assert any("count_match_rate" in reason for reason in verdict.reasons)


def test_count_mismatch_is_ignored_for_markdown() -> None:
    runs = [make_run(1, count_matches=False, cases_total=0, positive=0, negative=0)]
    summary, _ = summarize_series(
        runs,
        artifact_type=ArtifactType.TESTPLAN,
        expected_count=5,
        temperature=0.7,
        thresholds=Thresholds(),
    )
    assert "tc_jaccard_mean" in summary


def test_low_tc_jaccard_fails() -> None:
    runs = [
        make_run(1, tc_ids=("TC-01",)),
        make_run(2, tc_ids=("TC-02",)),
    ]
    summary, verdict = summarize_series(
        runs,
        artifact_type=ArtifactType.TESTCASES,
        expected_count=5,
        temperature=0.7,
        thresholds=Thresholds(tc_jaccard_mean_min=0.4),
    )
    assert summary["tc_jaccard_mean"] == 0.0
    assert verdict.status == "fail"
    assert any("tc_jaccard_mean" in reason for reason in verdict.reasons)


def test_low_br_coverage_fails() -> None:
    runs = [make_run(1, br_coverage=0.5)]
    summary, verdict = summarize_series(
        runs,
        artifact_type=ArtifactType.TESTCASES,
        expected_count=5,
        temperature=0.7,
        thresholds=Thresholds(br_coverage_min=0.8),
    )
    assert summary["br_coverage_min"] == 0.5
    assert verdict.status == "fail"
    assert any("br_coverage_min" in reason for reason in verdict.reasons)


def test_temperature_zero_without_identical_runs_warns() -> None:
    runs = [make_run(1, raw_sha256="a"), make_run(2, raw_sha256="b")]
    _, verdict = summarize_series(
        runs,
        artifact_type=ArtifactType.TESTCASES,
        expected_count=5,
        temperature=0.0,
        thresholds=Thresholds(),
    )
    assert verdict.status == "warn"
    assert any("temperature=0.0" in reason for reason in verdict.reasons)


def test_pass_at_1_requires_first_attempt() -> None:
    runs = [make_run(1, attempts=1), make_run(2, attempts=3)]
    summary, _ = summarize_series(
        runs,
        artifact_type=ArtifactType.TESTCASES,
        expected_count=5,
        temperature=0.7,
        thresholds=Thresholds(),
    )
    assert summary["pass_at_1"] == 0.5
    assert summary["pass_all"] == 1.0
    assert summary["mean_attempts"] == 2.0


def test_cell_summary_maps_fields() -> None:
    runs = [make_run(1), make_run(2)]
    summary, _ = summarize_series(
        runs,
        artifact_type=ArtifactType.TESTCASES,
        expected_count=5,
        temperature=0.7,
        thresholds=Thresholds(),
    )
    cell = cell_summary(
        "llama3",
        runs,
        summary,
        SeriesOptions(runs=2, temperature=0.1, seed=9, num_ctx=2048, num_predict=64),
        digest="abc",
    )
    assert cell.model == "llama3"
    assert cell.digest == "abc"
    assert cell.runs == 2
    assert cell.params.temperature == 0.1
    assert cell.params.seed == 9
    assert cell.tc_jaccard_mean == 1.0


class FakeLabClient:
    def __init__(self, answers: list[str]) -> None:
        self.answers = list(answers)

    def chat(self, system: str, user: str, temperature: float, **_: Any) -> str:
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


def test_run_consistency_returns_summary(
    normalized: NormalizedInput,
    prompt: PromptBundle,
    prompt_dir: Path,
) -> None:
    client = FakeLabClient([payload(), payload()])
    result = run_consistency(
        client,  # type: ignore[arg-type]
        normalized,
        prompt,
        options=SeriesOptions(runs=2, temperature=0.0),
        thresholds=Thresholds(),
        prompt_dir=prompt_dir,
    )
    assert len(result.runs) == 2
    assert result.summary["runs"] == 2
    assert result.summary["validator_pass_rate"] == 1.0
    assert result.verdict.status == "pass"
