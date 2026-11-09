"""Degradation statistics: baseline vs candidate cells."""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Thresholds
from .models import (
    CellSummary,
    ExperimentReport,
    NormalizedInput,
    PromptBundle,
    RunRecord,
    VerdictInfo,
)
from .ollama import LabOllamaClient
from .probes.consistency import cell_summary, run_consistency
from .runner import SeriesOptions

DEGRADATION_MIN_RUNS = 5
BOOTSTRAP_ITERATIONS = 1000
BOOTSTRAP_SEED = 12345
WILSON_Z = 1.96


class DegradationError(Exception):
    """Baseline/candidate inputs are incompatible (exit code 4)."""


@dataclass(frozen=True)
class DegradationResult:
    baseline_model: str
    candidate_model: str
    baseline_summary: dict[str, Any]
    candidate_summary: dict[str, Any]
    baseline: CellSummary
    candidate: CellSummary
    runs: list[RunRecord]
    comparison: dict[str, Any]
    verdict: VerdictInfo


def wilson_interval(successes: int, trials: int, z: float = WILSON_Z) -> tuple[float, float]:
    if trials <= 0:
        return (0.0, 1.0)
    p = successes / trials
    z2 = z * z
    denominator = 1 + z2 / trials
    centre = (p + z2 / (2 * trials)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z2 / (4 * trials)) / trials) / denominator
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def bootstrap_ci(
    values: Sequence[float],
    *,
    iterations: int = BOOTSTRAP_ITERATIONS,
    seed: int = BOOTSTRAP_SEED,
    confidence: float = 0.95,
) -> tuple[float, float]:
    """Percentile bootstrap for the mean, using only ``random.Random``."""
    if not values:
        return (0.0, 0.0)
    if len(values) == 1:
        return (float(values[0]), float(values[0]))
    rng = random.Random(seed)
    size = len(values)
    means: list[float] = []
    for _ in range(iterations):
        sample = [values[rng.randrange(size)] for _ in range(size)]
        means.append(sum(sample) / size)
    means.sort()
    lower = means[int((1 - confidence) / 2 * iterations)]
    upper = means[min(iterations - 1, int((1 + confidence) / 2 * iterations))]
    return (lower, upper)


def cohens_h(p1: float, p2: float) -> float:
    return 2 * math.asin(math.sqrt(min(1.0, max(0.0, p2)))) - 2 * math.asin(
        math.sqrt(min(1.0, max(0.0, p1)))
    )


def compare_summaries(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    thresholds: Thresholds,
    baseline_runs: int,
    candidate_runs: int,
    baseline_values: Sequence[float] | None = None,
    candidate_values: Sequence[float] | None = None,
) -> tuple[dict[str, Any], VerdictInfo]:
    base_pass = float(baseline.get("pass_at_1", 0.0))
    cand_pass = float(candidate.get("pass_at_1", 0.0))
    base_ci = wilson_interval(round(base_pass * baseline_runs), baseline_runs)
    cand_ci = wilson_interval(round(cand_pass * candidate_runs), candidate_runs)
    overlap = not (base_ci[1] < cand_ci[0] or cand_ci[1] < base_ci[0])

    base_br = int(baseline.get("invented_br_total", 0))
    cand_br = int(candidate.get("invented_br_total", 0))
    base_cov = float(baseline.get("br_coverage_mean", 0.0))
    cand_cov = float(candidate.get("br_coverage_mean", 0.0))
    base_json = float(baseline.get("valid_json_rate", 0.0))
    cand_json = float(candidate.get("valid_json_rate", 0.0))
    base_attempts = float(baseline.get("mean_attempts", 0.0))
    cand_attempts = float(candidate.get("mean_attempts", 0.0))

    diff = cand_pass - base_pass
    relative = (diff / base_pass) if base_pass else 0.0
    comparison: dict[str, Any] = {
        "pass_at_1": {
            "baseline": round(base_pass, 4),
            "candidate": round(cand_pass, 4),
            "diff": round(diff, 4),
            "ci_baseline": [round(item, 4) for item in base_ci],
            "ci_candidate": [round(item, 4) for item in cand_ci],
            "overlap": overlap,
        },
        "invented_br_total": {
            "baseline": base_br,
            "candidate": cand_br,
            "diff": cand_br - base_br,
        },
        "br_coverage_mean": {
            "baseline": round(base_cov, 4),
            "candidate": round(cand_cov, 4),
            "diff": round(cand_cov - base_cov, 4),
        },
        "valid_json_rate": {
            "baseline": round(base_json, 4),
            "candidate": round(cand_json, 4),
            "diff": round(cand_json - base_json, 4),
        },
        "mean_attempts": {
            "baseline": round(base_attempts, 4),
            "candidate": round(cand_attempts, 4),
            "diff": round(cand_attempts - base_attempts, 4),
        },
        "effect_size_h": round(cohens_h(base_pass, cand_pass), 4),
        "relative_diff": round(relative, 4),
    }
    if baseline_values is not None and candidate_values is not None:
        comparison["bootstrap_pass_at_1"] = {
            "baseline": [round(item, 4) for item in bootstrap_ci(baseline_values)],
            "candidate": [round(item, 4) for item in bootstrap_ci(candidate_values)],
        }

    if baseline_runs < DEGRADATION_MIN_RUNS or candidate_runs < DEGRADATION_MIN_RUNS:
        return (
            comparison,
            VerdictInfo(
                status="insufficient_data",
                reasons=[
                    f"need at least {DEGRADATION_MIN_RUNS} runs per cell: "
                    f"baseline={baseline_runs}, candidate={candidate_runs}"
                ],
                thresholds=_thresholds(thresholds),
            ),
        )

    regression: list[str] = []
    improvement: list[str] = []
    if diff <= -thresholds.degradation_pass_drop_max and not overlap:
        regression.append(
            f"pass@1 dropped by {-diff:.2f} (>= {thresholds.degradation_pass_drop_max:.2f}) "
            "with non-overlapping Wilson intervals"
        )
    if diff >= thresholds.degradation_pass_drop_max and not overlap:
        improvement.append(
            f"pass@1 improved by {diff:.2f} (>= {thresholds.degradation_pass_drop_max:.2f}) "
            "with non-overlapping Wilson intervals"
        )
    if cand_br - base_br >= 2:
        regression.append(f"invented_br_total increased by {cand_br - base_br} (>= 2)")
    if base_br - cand_br >= 2:
        improvement.append(f"invented_br_total decreased by {base_br - cand_br} (>= 2)")
    if cand_cov - base_cov <= -thresholds.degradation_br_drop_max:
        regression.append(
            f"br_coverage_mean dropped by {base_cov - cand_cov:.2f} "
            f"(>= {thresholds.degradation_br_drop_max:.2f})"
        )
    if cand_cov - base_cov >= thresholds.degradation_br_drop_max:
        improvement.append(
            f"br_coverage_mean improved by {cand_cov - base_cov:.2f} "
            f"(>= {thresholds.degradation_br_drop_max:.2f})"
        )
    if cand_json - base_json <= -thresholds.degradation_json_drop_max:
        regression.append(
            f"valid_json_rate dropped by {base_json - cand_json:.2f} "
            f"(>= {thresholds.degradation_json_drop_max:.2f})"
        )
    if cand_json - base_json >= thresholds.degradation_json_drop_max:
        improvement.append(
            f"valid_json_rate improved by {cand_json - base_json:.2f} "
            f"(>= {thresholds.degradation_json_drop_max:.2f})"
        )
    if cand_attempts - base_attempts >= thresholds.degradation_attempts_increase_max:
        regression.append(
            f"mean_attempts increased by {cand_attempts - base_attempts:.2f} "
            f"(>= {thresholds.degradation_attempts_increase_max:.2f})"
        )
    if base_attempts - cand_attempts >= thresholds.degradation_attempts_increase_max:
        improvement.append(
            f"mean_attempts decreased by {base_attempts - cand_attempts:.2f} "
            f"(>= {thresholds.degradation_attempts_increase_max:.2f})"
        )

    if regression:
        status, reasons = "regressed", regression
    elif improvement:
        status, reasons = "improved", improvement
    else:
        status, reasons = "unchanged", []
    return (
        comparison,
        VerdictInfo(status=status, reasons=reasons, thresholds=_thresholds(thresholds)),
    )


def run_degradation(
    client: LabOllamaClient,
    normalized: NormalizedInput,
    prompt: PromptBundle,
    *,
    baseline_model: str | None,
    candidate_model: str,
    options: SeriesOptions,
    thresholds: Thresholds,
    prompt_dir: Path | None = None,
    runs_dir: Path | None = None,
    baseline_report: ExperimentReport | None = None,
    progress: Callable[[str, int, int, RunRecord], None] | None = None,
) -> DegradationResult:
    if (baseline_model is None) == (baseline_report is None):
        raise DegradationError(
            "exactly one baseline source is required: --baseline-model or --baseline-report"
        )

    baseline_values: list[float] | None = None
    candidate_values: list[float] | None = None
    all_runs: list[RunRecord] = []

    if baseline_report is not None:
        _validate_baseline_report(baseline_report, prompt)
        baseline_model = baseline_report.environment.model
        baseline_summary = dict(baseline_report.summary)
        baseline_runs_count = int(
            baseline_summary.get("runs", baseline_report.params.get("runs", 0)) or 0
        )
        baseline_params = _options_from_report(baseline_report, fallback=options)
    else:
        assert baseline_model is not None
        baseline = run_consistency(
            client,
            normalized,
            prompt,
            options=options,
            thresholds=thresholds,
            prompt_dir=prompt_dir,
            runs_dir=runs_dir / "baseline" if runs_dir is not None else None,
            model=baseline_model,
            progress=(lambda index, total, record: progress("baseline", index, total, record))
            if progress is not None
            else None,
        )
        baseline_summary = baseline.summary
        baseline_runs_count = len(baseline.runs)
        baseline_params = baseline.params
        baseline_values = [
            1.0 if run.validator_pass and run.attempts == 1 else 0.0 for run in baseline.runs
        ]
        all_runs.extend(run.model_copy(update={"cell": "baseline"}) for run in baseline.runs)

    candidate = run_consistency(
        client,
        normalized,
        prompt,
        options=options,
        thresholds=thresholds,
        prompt_dir=prompt_dir,
        runs_dir=runs_dir / "candidate" if runs_dir is not None else None,
        model=candidate_model,
        progress=(lambda index, total, record: progress("candidate", index, total, record))
        if progress is not None
        else None,
    )
    candidate_summary = candidate.summary
    candidate_runs_count = len(candidate.runs)
    candidate_values = [
        1.0 if run.validator_pass and run.attempts == 1 else 0.0 for run in candidate.runs
    ]
    all_runs.extend(run.model_copy(update={"cell": "candidate"}) for run in candidate.runs)

    comparison, verdict = compare_summaries(
        baseline_summary,
        candidate_summary,
        thresholds=thresholds,
        baseline_runs=baseline_runs_count,
        candidate_runs=candidate_runs_count,
        baseline_values=baseline_values,
        candidate_values=candidate_values,
    )

    baseline_cell = cell_summary(
        baseline_model,
        [],
        baseline_summary,
        baseline_params,
        digest=baseline_report.environment.digest if baseline_report else None,
    )
    candidate_cell = cell_summary(
        candidate_model,
        candidate.runs,
        candidate_summary,
        candidate.params,
    )
    return DegradationResult(
        baseline_model=baseline_model,
        candidate_model=candidate_model,
        baseline_summary=baseline_summary,
        candidate_summary=candidate_summary,
        baseline=baseline_cell,
        candidate=candidate_cell,
        runs=all_runs,
        comparison=comparison,
        verdict=verdict,
    )


def _validate_baseline_report(report: ExperimentReport, prompt: PromptBundle) -> None:
    if report.prompt.prompt_sha256 != prompt.prompt_sha256:
        raise DegradationError("baseline prompt_sha256 does not match the candidate prompt")
    if report.prompt.artifact_type != prompt.artifact_type.value:
        raise DegradationError("baseline artifact_type does not match the candidate")
    if report.prompt.count != prompt.count:
        raise DegradationError("baseline count does not match the candidate")
    if not report.summary:
        raise DegradationError("baseline report has no summary")


def _options_from_report(
    report: ExperimentReport,
    *,
    fallback: SeriesOptions,
) -> SeriesOptions:
    params = report.environment
    summary_runs = report.params.get("runs")
    runs = (
        int(summary_runs) if summary_runs is not None else int(report.summary.get("runs", 0) or 0)
    )
    return SeriesOptions(
        runs=max(1, runs),
        temperature=params.temperature,
        seed=params.seed,
        num_ctx=params.num_ctx,
        num_predict=params.num_predict,
        max_retries=params.max_retries if params.max_retries is not None else fallback.max_retries,
    )


def _thresholds(thresholds: Thresholds) -> dict[str, float]:
    return {
        "degradation_pass_drop_max": thresholds.degradation_pass_drop_max,
        "degradation_br_drop_max": thresholds.degradation_br_drop_max,
        "degradation_json_drop_max": thresholds.degradation_json_drop_max,
        "degradation_attempts_increase_max": thresholds.degradation_attempts_increase_max,
        "min_runs": float(DEGRADATION_MIN_RUNS),
    }
