"""Live hallucination probe run."""

from __future__ import annotations

from pathlib import Path

import pytest

from llm_degradation.config import LabConfig
from llm_degradation.ollama import LabOllamaClient
from llm_degradation.probes.hallucinations import load_probes, run_hallucinations
from llm_degradation.runner import SeriesOptions

pytestmark = [pytest.mark.integration, pytest.mark.slow]


def test_golden_probes_run_against_the_model(
    live_client: LabOllamaClient,
    live_config: LabConfig,
    golden_dir: Path,
    repo_root: Path,
) -> None:
    probes = load_probes(golden_dir / "probes" / "hallucinations.json")
    result = run_hallucinations(
        live_client,
        probes,
        model=live_config.model,
        root=repo_root,
        golden_dir=golden_dir,
        options=SeriesOptions(runs=1, temperature=0.0, max_retries=0),
        runs=1,
        strict=False,
        thresholds=live_config.thresholds,
        normalizer_config=live_config.normalizer_config,
        prompt_dir=live_config.prompt_dir,
    )

    assert len(result.probes) == len(probes)
    assert result.summary["runs_total"] == len(probes)
    assert result.summary["hallucination_rate"] == round(
        result.summary["failed_probes"] / len(probes), 4
    )
    assert {item.id for item in result.probes} == {item.id for item in probes}
    assert result.verdict.status in {"pass", "warn", "fail"}
