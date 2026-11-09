"""Live normalizer canary."""

from __future__ import annotations

import pytest

from llm_degradation.config import LabConfig
from llm_degradation.models import NormalizedInput
from llm_degradation.ollama import LabOllamaClient
from llm_degradation.probes.canary import run_canary
from llm_degradation.runner import SeriesOptions

pytestmark = [pytest.mark.integration, pytest.mark.slow]


def test_canary_returns_a_parsed_result(
    live_client: LabOllamaClient,
    live_config: LabConfig,
    normalized: NormalizedInput,
) -> None:
    run = run_canary(
        live_client,
        normalized,
        model=live_config.model,
        runs=1,
        options=SeriesOptions(
            runs=1,
            temperature=0.0,
            seed=live_config.seed,
            num_ctx=live_config.num_ctx,
            num_predict=live_config.num_predict,
        ),
        thresholds=live_config.thresholds,
    )

    assert run.verdict in {"pass", "warn", "fail"}
    assert len(run.results) == 1
    result = run.results[0]
    assert result.parse_error is not None or result.llm_canonical
    assert 0.0 <= result.body_overlap_mean <= 1.0
