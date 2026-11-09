"""Live consistency series."""

from __future__ import annotations

from pathlib import Path

import pytest

from llm_degradation.config import LabConfig
from llm_degradation.models import NormalizedInput, PromptBundle
from llm_degradation.ollama import LabOllamaClient
from llm_degradation.probes.consistency import run_consistency
from llm_degradation.runner import SeriesOptions

pytestmark = [pytest.mark.integration, pytest.mark.slow]


def test_consistency_runs_twice(
    live_client: LabOllamaClient,
    live_config: LabConfig,
    normalized: NormalizedInput,
    prompt: PromptBundle,
    prompt_dir: Path,
) -> None:
    result = run_consistency(
        live_client,
        normalized,
        prompt,
        options=SeriesOptions(
            runs=2,
            temperature=live_config.temperature,
            seed=live_config.seed,
            num_ctx=live_config.num_ctx,
            num_predict=live_config.num_predict,
            max_retries=live_config.max_retries,
        ),
        thresholds=live_config.thresholds,
        prompt_dir=prompt_dir,
    )

    assert len(result.runs) == 2
    assert result.summary["runs"] == 2
    assert 0.0 <= result.summary["validator_pass_rate"] <= 1.0
    assert 0.0 <= result.summary["br_coverage_mean"] <= 1.0
    assert result.verdict.status in {"pass", "warn", "fail"}
