"""Streamlit page helpers and a render smoke test."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from ai_testgen.models import ArtifactType

from llm_degradation.config import LabConfig
from llm_degradation.models import (
    EnvironmentInfo,
    ExperimentReport,
    NormalizedInput,
    PromptBundle,
    VerdictInfo,
)
from llm_degradation.report import create_run, write_report
from llm_degradation.runner import SeriesOptions
from llm_degradation.ui import page


class FakeSt:
    def __init__(self) -> None:
        self.session_state: dict[str, Any] = {}
        self.calls: list[tuple[str, Any]] = []

    def header(self, value: str) -> None:
        self.calls.append(("header", value))

    def caption(self, value: str) -> None:
        self.calls.append(("caption", value))

    def error(self, value: str) -> None:
        self.calls.append(("error", value))

    def success(self, value: str) -> None:
        self.calls.append(("success", value))

    def tabs(self, labels: list[str]) -> list[Any]:
        self.calls.append(("tabs", labels))
        return [_Context() for _ in labels]


class _Context:
    def __enter__(self) -> _Context:
        return self

    def __exit__(self, *_args: object) -> None:
        pass


class _Bar:
    def __init__(self) -> None:
        self.updates: list[tuple[float, str | None]] = []

    def progress(self, value: float, text: str | None = None) -> None:
        self.updates.append((value, text))


class _Box:
    def __init__(self) -> None:
        self.captions: list[str] = []

    def caption(self, value: str) -> None:
        self.captions.append(value)


class ProgressSt:
    def __init__(self) -> None:
        self.bar = _Bar()
        self.box = _Box()

    def progress(self, value: float = 0.0, text: str | None = None) -> _Bar:
        self.bar.progress(value, text)
        return self.bar

    def empty(self) -> _Box:
        return self.box


def test_format_duration() -> None:
    assert page._format_duration(0) == "0 с"
    assert page._format_duration(75) == "1 мин 15 с"
    assert page._format_duration(3661) == "1 ч 01 мин"


def test_progress_shows_percent_elapsed_and_eta(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = ProgressSt()
    monkeypatch.setattr(page, "st", fake)
    progress = page._LabProgress(4, "Тест")
    assert "0%" in (fake.bar.updates[-1][1] or "")
    progress.update(1, "step 1")
    value, text = fake.bar.updates[-1]
    assert value == 0.25
    assert "25%" in (text or "")
    assert "Прошло:" in fake.box.captions[-1]
    assert "Осталось:" in fake.box.captions[-1]
    elapsed = progress.finish()
    assert elapsed >= 0.0
    assert fake.bar.updates[-1][0] == 1.0
    assert "завершено за" in fake.box.captions[-1]


def test_artifact_type_mapping() -> None:
    assert page._artifact_type("testplan") is ArtifactType.TESTPLAN
    with pytest.raises(ValueError):
        page._artifact_type("unknown")


def test_options_respects_seed_checkbox(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSt()
    monkeypatch.setattr(page, "st", fake)
    fake.session_state = {
        "lab_seed_fixed": False,
        "lab_seed": 42,
        "lab_temperature": 0.3,
        "lab_num_ctx": 1024,
        "lab_num_predict": 128,
        "lab_max_retries": 2,
    }
    options = page._options(LabConfig(), 4)
    assert options.seed is None
    assert options.temperature == 0.3
    assert options.runs == 4

    fake.session_state["lab_seed_fixed"] = True
    assert page._options(LabConfig(), 1).seed == 42


def test_build_report_with_input(
    monkeypatch: pytest.MonkeyPatch,
    normalized: NormalizedInput,
    prompt: PromptBundle,
) -> None:
    fake = FakeSt()
    monkeypatch.setattr(page, "st", fake)
    options = SeriesOptions(runs=2, temperature=0.1, seed=7)
    report = page._build_report(
        LabConfig(),
        "consistency",
        normalized,
        prompt,
        options,
        {"runs": 2},
        VerdictInfo(status="pass"),
        runs=[],
    )
    assert report.kind == "consistency"
    assert report.input.path == str(normalized.source)
    assert report.input.br_ids == list(normalized.br_ids)
    assert report.prompt.prompt_sha256 == prompt.prompt_sha256
    assert report.environment.temperature == 0.1
    assert report.environment.seed == 7


def test_build_report_without_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(page, "st", FakeSt())
    report = page._build_report(
        LabConfig(),
        "api",
        None,
        None,
        SeriesOptions(runs=0),
        {"checks": []},
        VerdictInfo(status="warn"),
    )
    assert report.input.path == ""
    assert report.prompt.artifact_type == "testcases"
    assert report.verdict.status == "warn"


def test_store_report_writes_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = FakeSt()
    monkeypatch.setattr(page, "st", fake)
    context = create_run(tmp_path, "health")
    report = ExperimentReport(
        run_id="",
        kind="health",
        started_at="",
        finished_at="",
        environment=EnvironmentInfo(model="llama3"),
        verdict=VerdictInfo(status="pass"),
    )
    page._store_report(context, report, LabConfig(output_dir=tmp_path))
    assert (context.directory / "report.json").is_file()
    assert fake.session_state["lab_last_report"].endswith("report.json")
    assert any(name == "success" for name, _ in fake.calls)


def test_baseline_candidates_combines_golden_and_output(tmp_path: Path) -> None:
    golden = tmp_path / "golden"
    (golden / "baseline").mkdir(parents=True)
    (golden / "baseline" / "llama3.json").write_text("{}", encoding="utf-8")
    output = tmp_path / "output"
    context = create_run(output, "consistency")
    report = ExperimentReport(
        run_id=context.run_id,
        kind="consistency",
        started_at="2026-01-01T00:00:00Z",
        finished_at="2026-01-01T00:01:00Z",
        summary={"runs": 1},
    )
    write_report(context, report, root=tmp_path)

    config = LabConfig(golden_dir=golden, output_dir=output)
    candidates = page._baseline_candidates(config)
    assert any(item.endswith("llama3.json") for item in candidates)
    assert any(item.endswith("report.json") for item in candidates)


def test_stand_command_mentions_dz2() -> None:
    command = page._stand_command()
    assert "dz2" in command
    assert "up-cpu.ps1" in command


def test_render_page_dispatches_all_tabs(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSt()
    monkeypatch.setattr(page, "st", fake)
    monkeypatch.setattr(page, "load_config", lambda *a, **k: LabConfig())
    monkeypatch.setattr(page, "_sidebar", lambda config: fake.calls.append(("sidebar", None)))

    seen: list[str] = []
    for name in (
        "_tab_health",
        "_tab_hallucinations",
        "_tab_consistency",
        "_tab_api",
        "_tab_canary",
        "_tab_degradation",
        "_tab_last_run",
        "_tab_reports",
    ):
        monkeypatch.setattr(page, name, lambda config, _name=name: seen.append(_name))

    page.render_page()

    assert seen == [
        "_tab_health",
        "_tab_hallucinations",
        "_tab_consistency",
        "_tab_api",
        "_tab_canary",
        "_tab_degradation",
        "_tab_last_run",
        "_tab_reports",
    ]
    labels = next(value for name, value in fake.calls if name == "tabs")
    assert labels[0] == "Стенд"
    assert labels[-1] == "Отчёты"
    assert len(labels) == 8
