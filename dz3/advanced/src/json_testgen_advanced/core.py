"""Deterministic HTTP core: executes a validated test plan.

The core knows nothing about LLMs and never executes code — it only
performs HTTP requests, extracts values via JSONPath, evaluates expect
checks with the kit's reference evaluator, and produces a report-ready
execution tree. The transport is injectable so tests never hit the network.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from ohw_kit.checks import CheckError, CheckResult, evaluate, json_path

from json_testgen_advanced.plan import StepSpec, TestPlan, TestSpec


class StepFailed(Exception):
    """A step did not meet its expect criteria."""


@dataclass
class StepExecution:
    name: str
    on_fail: str
    method: str
    path: str
    status_code: int | None = None
    duration_ms: float = 0.0
    checks: list[CheckResult] = field(default_factory=list)
    ok: bool = False
    error: str | None = None
    skipped: bool = False


@dataclass
class TestExecution:
    name: str
    description: str
    started: bool = False
    ok: bool = False
    steps: list[StepExecution] = field(default_factory=list)
    cleanup: list[StepExecution] = field(default_factory=list)
    cleanup_warnings: list[str] = field(default_factory=list)


@dataclass
class PlanExecution:
    service: str
    tests: list[TestExecution] = field(default_factory=list)
    doc_mode: str = "unknown"

    @property
    def passed(self) -> int:
        return sum(1 for t in self.tests if t.ok)

    @property
    def failed(self) -> int:
        return sum(1 for t in self.tests if not t.ok)

    @property
    def total(self) -> int:
        return len(self.tests)


def _substitute(value: Any, variables: dict[str, Any]) -> Any:
    """Recursively replace ``{var}`` placeholders in strings."""
    if isinstance(value, str):
        out = value
        for key, val in variables.items():
            out = out.replace("{" + key + "}", str(val))
        return out
    if isinstance(value, dict):
        return {k: _substitute(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute(v, variables) for v in value]
    return value


def _extract(variables: dict[str, Any], body: Any, extract_map: dict[str, str]) -> None:
    """Save ``extract`` JSONPath values into ``variables`` (first match wins)."""
    for key, path in extract_map.items():
        try:
            matches = json_path(body, path)
        except (CheckError, TypeError, KeyError, IndexError):
            matches = []
        if matches and key not in variables:
            variables[key] = matches[0]


def execute_plan(
    plan: TestPlan,
    *,
    base_url: str,
    transport: httpx.BaseTransport | None = None,
) -> PlanExecution:
    """Run every test scenario of ``plan`` against ``base_url``."""
    execution = PlanExecution(service=plan.service, doc_mode=plan.doc_mode)
    with httpx.Client(base_url=base_url, timeout=30.0, transport=transport) as client:
        for test in plan.tests:
            execution.tests.append(_run_test(client, test))
    return execution


def _run_test(client: httpx.Client, test: TestSpec) -> TestExecution:
    result = TestExecution(name=test.name, description=test.description)
    variables: dict[str, Any] = dict(test.vars)
    aborted = False

    for step in test.steps:
        result.started = True
        se = _run_step(client, variables, step)
        result.steps.append(se)
        if not se.ok:
            aborted = True
            if step.on_fail == "abort":
                break

    if result.started:
        # Cleanup always runs after steps begin (success, failure, or abort),
        # does not change the test status; failing cleanup is only a warning.
        for step in test.cleanup:
            ce = _run_step(client, variables, step)
            result.cleanup.append(ce)
            if not ce.ok:
                reason = ce.error or f"cleanup шаг '{ce.name}' не прошёл"
                result.cleanup_warnings.append(f"{test.name}: {reason}")

    result.ok = bool(result.started) and not aborted and all(s.ok for s in result.steps)
    return result


def _run_step(
    client: httpx.Client,
    variables: dict[str, Any],
    step: StepSpec,
) -> StepExecution:
    se = StepExecution(
        name=step.name,
        on_fail=step.on_fail,
        method=step.request.method,
        path=step.request.path,
    )
    try:
        path = _substitute(step.request.path, variables)
        headers = _substitute(step.request.headers, variables)
        body = _substitute(step.request.body, variables)
        se.path = path

        start = time.perf_counter()
        response = client.request(
            step.request.method, path, headers=headers, json=body
        )
        se.duration_ms = round((time.perf_counter() - start) * 1000, 1)
        se.status_code = response.status_code

        try:
            resp_body = response.json()
        except ValueError:
            resp_body = response.text

        # extract first (so current-step values feed expect if referenced)
        _extract(variables, resp_body, step.extract)

        failures: list[str] = []
        if step.expect.status_code is not None and response.status_code != step.expect.status_code:
            failures.append(
                f"ожидался статус {step.expect.status_code}, получен {response.status_code}"
            )
        se.checks = evaluate(step.expect.checks, status_code=response.status_code, body=resp_body)
        for check in se.checks:
            if not check.ok:
                failures.append(check.as_line())

        se.ok = not failures
        if failures:
            se.error = "; ".join(failures)
    except httpx.HTTPError as exc:
        se.error = f"сетевая ошибка: {exc}"
        se.ok = False
    return se
