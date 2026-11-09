"""Consistency probe: one prompt, N runs, structural stability metrics."""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_testgen.models import ArtifactType

from llm_degradation.config import Thresholds
from llm_degradation.metrics import jaccard, pairwise_mean_min
from llm_degradation.models import (
    CellSummary,
    NormalizedInput,
    PromptBundle,
    RunRecord,
    VerdictInfo,
)
from llm_degradation.ollama import LabOllamaClient
from llm_degradation.runner import SeriesOptions, run_series


@dataclass(frozen=True)
class SeriesResult:
    runs: list[RunRecord]
    summary: dict[str, Any]
    verdict: VerdictInfo
    params: SeriesOptions


def summarize_series(
    runs: Sequence[RunRecord],
    *,
    artifact_type: ArtifactType,
    expected_count: int,
    temperature: float,
    thresholds: Thresholds,
) -> tuple[dict[str, Any], VerdictInfo]:
    total = len(runs)
    if total == 0:
        return (
            {"runs": 0, "pass_at_1": 0.0, "pass_all": 0.0},
            VerdictInfo(status="fail", reasons=["no runs recorded"]),
        )

    pass_at_1 = _rate(run.validator_pass and run.attempts == 1 for run in runs)
    pass_all = _rate(run.validator_pass for run in runs)
    valid_json_rate = _rate(run.metrics.json_valid for run in runs)
    count_match_rate = _rate(run.metrics.count_matches for run in runs)
    balance_match_rate = _rate(_balanced(run, expected_count) for run in runs)
    br_coverage_mean = round(_mean([run.metrics.br_coverage for run in runs]), 4)
    br_coverage_min = round(min(run.metrics.br_coverage for run in runs), 4)
    invented_br_total = sum(len(run.metrics.br_invented) for run in runs)
    invented_url_total = sum(len(run.metrics.urls_invented) for run in runs)
    mean_attempts = round(_mean([float(run.attempts) for run in runs]), 4)
    mean_latency_ms = round(_mean([run.latency_ms for run in runs]), 1)

    tc_sets = [set(run.metrics.tc_ids) for run in runs]
    heading_sets = [set(run.metrics.section_headings) for run in runs]
    title_sets = [set(run.metrics.title_tokens) for run in runs]

    tc_jaccard_mean, _ = pairwise_mean_min(tc_sets)
    heading_jaccard_mean, heading_jaccard_min = pairwise_mean_min(heading_sets)
    title_jaccard_mean, _ = pairwise_mean_min(title_sets)

    hashes = [run.raw_sha256 for run in runs if run.raw_sha256]
    unique_artifacts = len(set(hashes))
    identical_runs = _identical_pairs(hashes)

    available = [br_coverage_mean, heading_jaccard_mean]
    if artifact_type is ArtifactType.TESTCASES:
        available.append(tc_jaccard_mean)
    self_consistency = round(_mean(available), 4)

    summary: dict[str, Any] = {
        "runs": total,
        "pass_at_1": round(pass_at_1, 4),
        "pass_all": round(pass_all, 4),
        "valid_json_rate": round(valid_json_rate, 4),
        "validator_pass_rate": round(pass_all, 4),
        "count_match_rate": round(count_match_rate, 4),
        "balance_match_rate": round(balance_match_rate, 4),
        "br_coverage_mean": br_coverage_mean,
        "br_coverage_min": br_coverage_min,
        "invented_br_total": invented_br_total,
        "invented_url_total": invented_url_total,
        "tc_jaccard_mean": tc_jaccard_mean,
        "tc_jaccard_min": min(
            (
                jaccard(tc_sets[i], tc_sets[j])
                for i in range(len(tc_sets))
                for j in range(i + 1, len(tc_sets))
            ),
            default=1.0,
        ),
        "heading_jaccard_mean": heading_jaccard_mean,
        "heading_jaccard_min": heading_jaccard_min,
        "title_jaccard_mean": title_jaccard_mean,
        "identical_runs": identical_runs,
        "unique_artifacts": unique_artifacts,
        "mean_attempts": mean_attempts,
        "mean_latency_ms": mean_latency_ms,
        "self_consistency": self_consistency,
    }

    reasons: list[str] = []
    status = "pass"
    if pass_all < thresholds.validator_pass_rate_min:
        status = "fail"
        reasons.append(
            f"validator_pass_rate {pass_all:.2f} < {thresholds.validator_pass_rate_min:.2f}"
        )
    if artifact_type is ArtifactType.TESTCASES:
        if count_match_rate < thresholds.count_match_rate_min:
            status = "fail"
            reasons.append(
                f"count_match_rate {count_match_rate:.2f} < {thresholds.count_match_rate_min:.2f}"
            )
        if tc_jaccard_mean < thresholds.tc_jaccard_mean_min:
            status = "fail"
            reasons.append(
                f"tc_jaccard_mean {tc_jaccard_mean:.2f} < {thresholds.tc_jaccard_mean_min:.2f}"
            )
    if br_coverage_min < thresholds.br_coverage_min:
        status = "fail"
        reasons.append(f"br_coverage_min {br_coverage_min:.2f} < {thresholds.br_coverage_min:.2f}")
    if status != "fail" and identical_runs == 0 and temperature == 0.0:
        status = "warn"
        reasons.append("temperature=0.0 but no two runs produced identical artifacts")

    verdict = VerdictInfo(
        status=status,
        reasons=reasons,
        thresholds={
            "validator_pass_rate_min": thresholds.validator_pass_rate_min,
            "count_match_rate_min": thresholds.count_match_rate_min,
            "br_coverage_min": thresholds.br_coverage_min,
            "tc_jaccard_mean_min": thresholds.tc_jaccard_mean_min,
            "heading_jaccard_mean_min": thresholds.heading_jaccard_mean_min,
        },
    )
    return summary, verdict


def run_consistency(
    client: LabOllamaClient,
    normalized: NormalizedInput,
    prompt: PromptBundle,
    *,
    options: SeriesOptions,
    thresholds: Thresholds,
    prompt_dir: Path | None = None,
    runs_dir: Path | None = None,
    model: str | None = None,
    progress: Callable[[int, int, RunRecord], None] | None = None,
) -> SeriesResult:
    runs = run_series(
        client,
        normalized,
        prompt,
        artifact_type=prompt.artifact_type,
        count=prompt.count,
        options=options,
        prompt_dir=prompt_dir,
        runs_dir=runs_dir,
        model=model,
        progress=progress,
    )
    summary, verdict = summarize_series(
        runs,
        artifact_type=prompt.artifact_type,
        expected_count=prompt.count,
        temperature=options.temperature,
        thresholds=thresholds,
    )
    return SeriesResult(runs=runs, summary=summary, verdict=verdict, params=options)


def cell_summary(
    model: str,
    runs: Sequence[RunRecord],
    summary: dict[str, Any],
    options: SeriesOptions,
    digest: str | None = None,
) -> CellSummary:
    return CellSummary(
        model=model,
        digest=digest,
        params=_run_params(options),
        runs=len(runs),
        pass_at_1=float(summary.get("pass_at_1", 0.0)),
        pass_all=float(summary.get("pass_all", 0.0)),
        valid_json_rate=float(summary.get("valid_json_rate", 0.0)),
        validator_pass_rate=float(summary.get("validator_pass_rate", 0.0)),
        count_match_rate=float(summary.get("count_match_rate", 0.0)),
        br_coverage_mean=float(summary.get("br_coverage_mean", 0.0)),
        br_coverage_min=float(summary.get("br_coverage_min", 0.0)),
        invented_br_total=int(summary.get("invented_br_total", 0)),
        invented_url_total=int(summary.get("invented_url_total", 0)),
        mean_attempts=float(summary.get("mean_attempts", 0.0)),
        mean_latency_ms=float(summary.get("mean_latency_ms", 0.0)),
        tc_jaccard_mean=_optional_float(summary.get("tc_jaccard_mean")),
        identical_runs=int(summary.get("identical_runs", 0)),
        unique_artifacts=int(summary.get("unique_artifacts", 0)),
    )


def _run_params(options: SeriesOptions) -> Any:
    from llm_degradation.models import RunParams

    return RunParams(
        temperature=options.temperature,
        seed=options.seed,
        num_ctx=options.num_ctx,
        num_predict=options.num_predict,
        max_retries=options.max_retries,
    )


def _balanced(run: RunRecord, expected_count: int) -> bool:
    metrics = run.metrics
    if metrics.cases_total == 0:
        return False
    if expected_count >= 2 and (metrics.positive < 1 or metrics.negative < 1):
        return False
    if expected_count >= 5 and metrics.negative < math.ceil(expected_count / 3):
        return False
    return True


def _rate(values: Any) -> float:
    items = list(values)
    if not items:
        return 0.0
    return sum(1 for item in items if item) / len(items)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _identical_pairs(hashes: Sequence[str]) -> int:
    counts: dict[str, int] = {}
    for item in hashes:
        counts[item] = counts.get(item, 0) + 1
    return sum(count * (count - 1) // 2 for count in counts.values())


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)
