"""Report writing, reading and Markdown rendering."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from llm_degradation.models import (
    ArtifactMetrics,
    CellReport,
    EnvironmentInfo,
    ExperimentReport,
    InputInfo,
    PromptInfo,
    RunRecord,
    VerdictInfo,
)
from llm_degradation.report import (
    MANIFEST_JSON,
    NORMALIZED_MD,
    PROMPT_TXT,
    REPORT_JSON,
    REPORT_MD,
    ReportError,
    build_manifest,
    create_run,
    finalize_report,
    git_commit,
    list_reports,
    load_report,
    prune_reports,
    render_markdown,
    report_envelope,
    write_inputs,
    write_report,
)


def make_report(**updates: Any) -> ExperimentReport:
    data: dict[str, Any] = {
        "run_id": "run-1",
        "kind": "consistency",
        "started_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-01T00:01:00Z",
        "environment": EnvironmentInfo(
            base_url="http://ohw-ollama:11434",
            ollama_version="0.9.1",
            model="llama3",
            digest="abc",
        ),
        "input": InputInfo(
            path="golden/inputs/no-cart.md",
            encoding="utf-8",
            url="https://otus.ru/catalog/courses",
            br_ids=["BR-1", "BR-2"],
        ),
        "prompt": PromptInfo(
            artifact_type="testcases",
            count=5,
            prompt_sha256="prompt-hash",
        ),
        "params": {"runs": 2},
        "summary": {"runs": 2, "pass_at_1": 1.0, "nested": {"a": 1}},
        "verdict": VerdictInfo(status="pass"),
    }
    data.update(updates)
    return ExperimentReport(**data)


def make_run(index: int, cell: str = "baseline") -> RunRecord:
    return RunRecord(
        index=index,
        cell=cell,
        latency_ms=12.0,
        attempts=1,
        raw_sha256=f"hash-{index}",
        validator_pass=True,
        metrics=ArtifactMetrics(
            cases_total=5,
            positive=2,
            negative=3,
            br_coverage=1.0,
        ),
    )


def test_create_run_creates_layout(tmp_path: Path) -> None:
    context = create_run(tmp_path / "output", "consistency")
    assert context.directory.is_dir()
    assert (context.directory / "runs").is_dir()
    assert context.directory.name == context.run_id
    assert context.directory.parent == (tmp_path / "output").resolve()
    assert context.kind == "consistency"


def test_create_run_avoids_collisions(tmp_path: Path) -> None:
    first = create_run(tmp_path, "health")
    second = create_run(tmp_path, "health")
    assert first.directory != second.directory
    assert first.directory.is_dir()
    assert second.directory.is_dir()


def test_write_inputs(tmp_path: Path) -> None:
    context = create_run(tmp_path, "health")
    write_inputs(context, "normalized", "prompt")
    assert (context.directory / NORMALIZED_MD).read_text(encoding="utf-8") == "normalized"
    assert (context.directory / PROMPT_TXT).read_text(encoding="utf-8") == "prompt"


def test_git_commit_uses_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LLM_LAB_GIT_COMMIT", "deadbee")
    assert git_commit(tmp_path) == "deadbee"


def test_git_commit_returns_none_outside_repository(tmp_path: Path) -> None:
    assert git_commit(tmp_path) is None


def test_build_manifest_contains_prompt_and_commit(tmp_path: Path) -> None:
    manifest = build_manifest(make_report(), root=tmp_path)
    assert manifest["schema_version"] == 1
    assert manifest["prompt"]["prompt_sha256"] == "prompt-hash"
    assert manifest["git_commit"] is None
    assert manifest["params"] == {"runs": 2}


def test_write_report_writes_all_files(tmp_path: Path) -> None:
    context = create_run(tmp_path, "consistency")
    report = make_report(run_id=context.run_id)
    json_path, md_path = write_report(context, report, root=tmp_path)

    assert json_path == context.directory / REPORT_JSON
    assert md_path == context.directory / REPORT_MD
    assert (context.directory / MANIFEST_JSON).is_file()

    loaded = load_report(json_path)
    assert loaded.run_id == context.run_id
    assert "# Согласованность: llama3" in md_path.read_text(encoding="utf-8")


def test_write_report_exports_raw_model_attempts(tmp_path: Path) -> None:
    context = create_run(tmp_path, "consistency")
    run_dir = context.directory / "runs" / "run-01"
    run_dir.mkdir(parents=True)
    (run_dir / "request.json").write_text(
        json.dumps(
            {
                "messages": [{"role": "user", "content": "prompt"}],
                "attempts": [
                    {"index": 1, "raw": "bad json", "error": "invalid"},
                    {"index": 2, "raw": '{"cases": []}', "error": None},
                ],
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "response.txt").write_text('{"cases": []}', encoding="utf-8")
    write_report(context, make_report(run_id=context.run_id), root=tmp_path)

    raw_dir = context.directory / "raw" / "runs" / "run-01"
    assert (raw_dir / "attempt-01.txt").read_text(encoding="utf-8") == "bad json"
    assert json.loads((raw_dir / "attempt-02.json").read_text(encoding="utf-8"))["raw"] == (
        '{"cases": []}'
    )


def test_write_report_exports_text_probe_raw(tmp_path: Path) -> None:
    context = create_run(tmp_path, "hallucinations")
    probe_dir = context.directory / "probes" / "H-01" / "run-01"
    probe_dir.mkdir(parents=True)
    (probe_dir / "request.json").write_text(
        json.dumps({"messages": [{"role": "user", "content": "question"}]}),
        encoding="utf-8",
    )
    (probe_dir / "response.txt").write_text("model answer", encoding="utf-8")
    write_report(context, make_report(run_id=context.run_id, kind="hallucinations"), root=tmp_path)

    raw_dir = context.directory / "raw" / "probes" / "H-01" / "run-01"
    assert (raw_dir / "response.txt").read_text(encoding="utf-8") == "model answer"
    assert json.loads((raw_dir / "request.json").read_text(encoding="utf-8"))["messages"]


def test_finalize_report_fills_missing_fields(tmp_path: Path) -> None:
    context = create_run(tmp_path, "health")
    report = make_report(run_id="", started_at="", finished_at="")
    finalized = finalize_report(report, context)
    assert finalized.run_id == context.run_id
    assert finalized.started_at == context.started_at
    assert finalized.finished_at


def test_load_report_rejects_unsupported_schema(tmp_path: Path) -> None:
    path = tmp_path / REPORT_JSON
    path.write_text(json.dumps({"schema_version": 2}), encoding="utf-8")
    with pytest.raises(ReportError, match="schema_version"):
        load_report(path)


def test_load_report_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / REPORT_JSON
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ReportError, match="invalid JSON"):
        load_report(path)


def test_load_report_rejects_non_object(tmp_path: Path) -> None:
    path = tmp_path / REPORT_JSON
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ReportError, match="not a JSON object"):
        load_report(path)


def test_load_report_rejects_schema_violations(tmp_path: Path) -> None:
    payload = make_report().model_dump(mode="json")
    payload["unexpected"] = True
    path = tmp_path / REPORT_JSON
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ReportError, match="schema validation failed"):
        load_report(path)


def test_load_report_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ReportError, match="cannot read"):
        load_report(tmp_path / "missing.json")


def test_list_reports_sorts_and_skips_broken(tmp_path: Path) -> None:
    output = tmp_path / "output"
    for name in ("run-a", "run-b"):
        context = create_run(output, "health")
        write_report(context, make_report(run_id=name, kind="health"), root=tmp_path)
    broken = output / "broken"
    broken.mkdir()
    (broken / REPORT_JSON).write_text("{", encoding="utf-8")

    items = list_reports(output)
    assert len(items) == 2
    assert all(item["kind"] == "health" for item in items)


def test_list_reports_respects_limit(tmp_path: Path) -> None:
    for _ in range(3):
        context = create_run(tmp_path, "health")
        write_report(context, make_report(), root=tmp_path)
    assert len(list_reports(tmp_path, limit=2)) == 2


def test_list_reports_missing_directory(tmp_path: Path) -> None:
    assert list_reports(tmp_path / "nope") == []


def test_render_markdown_includes_runs_and_summary() -> None:
    report = make_report(
        runs=[make_run(1), make_run(2, "candidate")],
        verdict=VerdictInfo(status="fail", reasons=["something broke"]),
        warnings=["watch out"],
    )
    markdown = render_markdown(report)
    assert "# Согласованность: llama3" in markdown
    assert "## Прогоны" in markdown
    assert "| 1 | baseline | yes | 1 | 5 | 2 | 3 | 1.00 | 12 |" in markdown
    assert "## Сводка" in markdown
    assert "## Вердикт" in markdown
    assert "- something broke" in markdown
    assert "## Предупреждения" in markdown
    assert "- watch out" in markdown


def test_render_markdown_degradation_table() -> None:
    report = make_report(
        kind="degradation",
        baseline=CellReport(model="llama3"),
        candidate=CellReport(model="llama3:q4"),
        comparison={
            "pass_at_1": {"baseline": 1.0, "candidate": 0.5, "diff": -0.5},
            "effect_size_h": -1.2,
        },
    )
    markdown = render_markdown(report)
    assert "## Degradation" in markdown
    assert "| pass_at_1 | 1.0 | 0.5 | -0.5 |" in markdown
    assert "effect_size_h" not in markdown.split("## Degradation")[1].split("## Файлы")[0]


def test_render_markdown_canary_section() -> None:
    report = make_report(
        kind="canary",
        summary={"canary": {"ids_exact": True, "title_overlap": 1.0}},
    )
    markdown = render_markdown(report)
    assert "## Канарейка" in markdown
    assert "- ids_exact: True" in markdown
    assert "- title_overlap: 1.0" in markdown


def test_report_envelope_marks_failure() -> None:
    report = make_report(verdict=VerdictInfo(status="fail", reasons=["bad"]))
    envelope = report_envelope(report, Path("output/report.json"))
    assert envelope["ok"] is False
    assert envelope["verdict"] == "fail"
    assert envelope["error"] is None
    assert envelope["report"]["run_id"] == "run-1"


def test_prune_reports_keeps_newest(tmp_path: Path) -> None:
    (tmp_path / ".gitkeep").write_text("", encoding="utf-8")
    for index in range(5):
        context = create_run(tmp_path, "health")
        report = make_report(run_id=f"2026010{index}T000000Z-aaaaaa", kind="health")
        write_report(context, report, root=tmp_path)
    removed = prune_reports(tmp_path, 2)
    assert len(removed) == 3
    remaining = sorted(item.name for item in tmp_path.iterdir() if item.is_dir())
    assert len(remaining) == 2
    assert (tmp_path / ".gitkeep").is_file()


def test_write_report_keep_runs_prunes_old(tmp_path: Path) -> None:
    for index in range(4):
        context = create_run(tmp_path, "health")
        report = make_report(run_id=f"2026010{index}T000000Z-aaaaaa", kind="health")
        write_report(context, report, root=tmp_path, keep_runs=2)
    reports = list_reports(tmp_path)
    assert len(reports) == 2
    assert {item["run_id"] for item in reports} == {
        "20260103T000000Z-aaaaaa",
        "20260102T000000Z-aaaaaa",
    }
