"""Live API contract checks."""

from __future__ import annotations

import pytest

from llm_degradation.ollama import LabOllamaClient
from llm_degradation.probes.api_contract import api_verdict, run_api_checks

pytestmark = pytest.mark.integration


def test_all_critical_contract_checks_pass(live_client: LabOllamaClient) -> None:
    checks = run_api_checks(live_client, live_client.model)
    assert len(checks) == 12
    failed = [item.id for item in checks if item.status == "fail" and item.critical]
    assert failed == []
    assert api_verdict(checks).status in {"pass", "warn"}
