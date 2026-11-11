"""Tests for the API-test seam: contract, persistence and the report section.

The tests never touch the network. A fake runner stands in for a future DZ3
advanced adapter, which keeps the seam honest: the workflow only depends on the
``ApiTestRunner`` protocol.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.autotests import (
    ApiCaseResult,
    ApiTestError,
    ApiTestReport,
    ApiTestRequest,
    load_api_tests,
    run_api_tests,
    write_api_tests,
)
from src.autotests.disabled import DisabledApiTestRunner
from src.models import PipelineData
from src.reporting.report_generator import render_markdown, write_json_report
from src.workflow.main import EXIT_OK, EXIT_USAGE, build_parser, main, selected_phases


class FakeRunner:
    """Deterministic runner used instead of a real adapter."""

    name = "fake"

    def __init__(self, report: ApiTestReport | None = None) -> None:
        self.report = report
        self.requests: list[ApiTestRequest] = []

    def run(self, request: ApiTestRequest) -> ApiTestReport:
        self.requests.append(request)
        if self.report is not None:
            return self.report
        return ApiTestReport.from_cases(
            runner=self.name,
            base_url=request.base_url,
            model=request.model,
            generated_at="2026-11-11T12:00:00+00:00",
            duration_ms=12.0,
            cases=[
                ApiCaseResult(
                    name="health",
                    method="GET",
                    path="/health",
                    ok=True,
                    status_code=200,
                    duration_ms=3.0,
                ),
                ApiCaseResult(
                    name="create",
                    method="POST",
                    path="/items",
                    ok=False,
                    status_code=500,
                    duration_ms=9.0,
                    error="unexpected status",
                ),
            ],
        )


def _report() -> ApiTestReport:
    return FakeRunner().run(
        ApiTestRequest(
            base_url="http://api.test",
            model="qwen2.5:7b-instruct",
        )
    )


def test_from_cases_computes_totals_and_keeps_errors() -> None:
    report = ApiTestReport.from_cases(
        runner="fake",
        base_url="http://api.test",
        model="qwen2.5:7b-instruct",
        generated_at="2026-11-11T12:00:00+00:00",
        cases=[
            ApiCaseResult(name="ok", ok=True),
            ApiCaseResult(name="bad", ok=False, error="boom"),
            ApiCaseResult(name="also-ok", ok=True),
        ],
        errors=["boom"],
        duration_ms=15.0,
    )
    assert (report.total, report.passed, report.failed) == (3, 2, 1)
    assert report.errors == ["boom"]
    assert report.cases[1].error == "boom"


def test_report_json_round_trip() -> None:
    report = _report()
    restored = ApiTestReport.from_dict(report.to_dict())
    assert restored == report
    assert restored.to_dict() == report.to_dict()


def test_write_and_load_api_tests(tmp_path: Path) -> None:
    report = _report()
    path = write_api_tests(report, tmp_path)
    assert path == tmp_path / "api-tests.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["total"] == 2
    assert raw["cases"][0]["name"] == "health"

    loaded = load_api_tests(tmp_path)
    assert loaded == report


def test_load_api_tests_returns_none_without_file(tmp_path: Path) -> None:
    assert load_api_tests(tmp_path) is None


def test_disabled_runner_fails_loudly() -> None:
    runner = DisabledApiTestRunner()
    assert runner.name == "disabled"
    with pytest.raises(ApiTestError, match="адаптер API-тестов не подключён"):
        run_api_tests(ApiTestRequest(base_url="http://api.test"))


def test_markdown_contains_api_tests_section_only_with_data() -> None:
    data = PipelineData(model="qwen2.5:7b-instruct")
    without = render_markdown(data)
    assert "API-тесты" not in without

    data.api_tests = _report()
    with_section = render_markdown(data)
    assert "## API-тесты" in with_section
    assert "| API-тесты | да |" in with_section
    assert "`fake`" in with_section
    assert "http://api.test" in with_section
    assert "| health | GET | /health | 200 | 3 | OK |" in with_section
    assert "unexpected status" in with_section


def test_report_dict_omits_api_tests_when_absent() -> None:
    data = PipelineData(model="qwen2.5:7b-instruct")
    assert "api_tests" not in data.to_report_dict()

    data.api_tests = _report()
    payload = data.to_report_dict()
    assert payload["api_tests"]["total"] == 2
    assert PipelineData.from_report_dict(payload).api_tests == data.api_tests


def test_selected_phases_include_api_tests_only_on_flag() -> None:
    args = build_parser().parse_args([])
    assert selected_phases(args) == ["generate", "assess", "visualize", "report"]

    args = build_parser().parse_args(["--api-tests"])
    assert selected_phases(args) == ["api-tests"]


def test_api_tests_without_base_url_returns_usage_error(tmp_path: Path) -> None:
    code = main(["--api-tests", "--output-dir", str(tmp_path)])
    assert code == EXIT_USAGE


def test_api_tests_without_adapter_returns_usage_error(tmp_path: Path) -> None:
    code = main(
        [
            "--api-tests",
            "--api-base-url",
            "http://api.test",
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert code == EXIT_USAGE


def test_api_tests_with_fake_runner_writes_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("src.workflow.main.resolve_runner", FakeRunner)
    write_json_report(PipelineData(model="qwen2.5:7b-instruct"), tmp_path)
    code = main(
        [
            "--api-tests",
            "--api-base-url",
            "http://api.test/v1/",
            "--api-cases",
            "2",
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert code == EXIT_OK
    raw: dict[str, Any] = json.loads(
        (tmp_path / "api-tests.json").read_text(encoding="utf-8")
    )
    assert raw["runner"] == "fake"
    assert raw["base_url"] == "http://api.test/v1"
    assert raw["total"] == 2

    metrics: dict[str, Any] = json.loads(
        (tmp_path / "quality-metrics.json").read_text(encoding="utf-8")
    )
    assert metrics["api_tests"]["total"] == 2
    assert metrics["averages"]["overall"] is None


def test_report_restores_api_tests_from_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("src.workflow.main.resolve_runner", FakeRunner)
    assert (
        main(
            [
                "--api-tests",
                "--api-base-url",
                "http://api.test",
                "--output-dir",
                str(tmp_path),
            ]
        )
        == EXIT_OK
    )
    write_json_report(PipelineData(model="qwen2.5:7b-instruct"), tmp_path)

    assert main(["--report", "--output-dir", str(tmp_path)]) == EXIT_OK
    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "## API-тесты" in markdown
    assert "health" in markdown
