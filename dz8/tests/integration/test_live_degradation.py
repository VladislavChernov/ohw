"""Live degradation comparison (same model for baseline and candidate)."""

from __future__ import annotations

from pathlib import Path

import pytest

from llm_degradation.config import LabConfig
from llm_degradation.degradation import run_degradation
from llm_degradation.models import NormalizedInput, PromptBundle
from llm_degradation.ollama import LabOllamaClient
from llm_degradation.runner import SeriesOptions

pytestmark = [pytest.mark.integration, pytest.mark.slow]


def test_degradation_compares_two_cells(
    live_client: LabOllamaClient,
    live_config: LabConfig,
    normalized: NormalizedInput,
    prompt: PromptBundle,
    prompt_dir: Path,
) -> None:
    result = run_degradation(
        live_client,
        normalized,
        prompt,
        baseline_model=live_config.model,
        candidate_model=live_config.model,
        options=SeriesOptions(
            runs=5,
            temperature=live_config.temperature,
            seed=live_config.seed,
            num_ctx=live_config.num_ctx,
            num_predict=live_config.num_predict,
            max_retries=live_config.max_retries,
        ),
        thresholds=live_config.thresholds,
        prompt_dir=prompt_dir,
    )

    assert result.baseline_summary["runs"] == 5
    assert result.candidate_summary["runs"] == 5
    assert {"pass_at_1", "effect_size_h", "relative_diff"} <= set(result.comparison)
    assert result.verdict.status in {
        "unchanged",
        "improved",
        "regressed",
        "insufficient_data",
    }
