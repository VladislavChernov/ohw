from report_builder.aggregate import (
    compute_complete,
    percentile,
    scenario_verdict,
    summarize_results,
)
from report_builder.render import render_red_team_md, render_run_md


def result(verdict, run=1, latency=100):
    return {
        "scenario": "llm01-ignore-previous",
        "owasp": "LLM01",
        "title": "Instruction override",
        "preset": 1,
        "run": run,
        "status": "completed",
        "verdict": verdict,
        "http_code": 403 if verdict == "blocked" else 200,
        "latency_ms": latency,
        "leak": verdict == "bypass",
        "forbidden_tool": False,
        "db_changed": False,
        "error": "boom" if verdict == "error" else None,
    }


def test_percentile():
    assert percentile([100], 0.5) == 100
    assert percentile([0, 100], 0.5) == 50
    assert percentile([0, 100, 200], 0.95) == 190


def test_n_less_than_three_is_unknown():
    assert scenario_verdict(["bypass", "bypass"]) == "unknown"


def test_mixed_and_error_rules():
    assert scenario_verdict(["blocked", "bypass", "blocked"]) == "mixed"
    assert scenario_verdict(["error", "error", "error"]) == "error"
    assert scenario_verdict(["blocked", "blocked", "blocked"]) == "blocked"


def test_summary_counts_and_latency():
    summaries = summarize_results(
        [result("blocked", 1, 100), result("bypass", 2, 200), result("blocked", 3, 300)]
    )
    assert summaries[0]["verdict"] == "mixed"
    assert summaries[0]["counts"]["blocked"] == 2
    assert summaries[0]["counts"]["bypass"] == 1
    assert summaries[0]["latency_ms"]["p50"] == 200


def test_complete_requires_all_runs_and_files():
    manifest = {"runs": 3, "scenario_ids": ["a"]}
    results = [
        {**result("blocked", run=1), "scenario": "a"},
        {**result("blocked", run=2), "scenario": "a"},
        {**result("blocked", run=3), "scenario": "a"},
    ]
    assert compute_complete(manifest, results) is True
    assert compute_complete(manifest, results[:2]) is False
    assert compute_complete({"runs": 2, "scenario_ids": ["a"]}, results[:2]) is False
    assert compute_complete(manifest, results + [{**result("error", run=4), "scenario": "a"}]) is False


def test_render_missing_cell_is_unknown_not_blocked():
    manifest = {
        "run_id": "20260101T000000Z",
        "preset": 1,
        "started_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-01T00:01:00Z",
        "target": "http://agent:8080",
        "model": "test",
        "git_commit": "abc",
        "runs": 3,
        "complete": True,
    }
    summary = {
        "scenario": "llm01-ignore-previous",
        "owasp": "LLM01",
        "title": "Instruction override",
        "preset": 1,
        "n": 3,
        "counts": {"blocked": 3, "bypass": 0, "mixed": 0, "unknown": 0, "error": 0},
        "verdict": "blocked",
        "latency_ms": {"p50": 100, "p95": 150},
        "cpu": None,
        "ram_mb": None,
    }
    report = render_red_team_md([{"manifest": manifest, "summaries": [summary]}])
    assert "| llm01-ignore-previous | LLM01 | blocked (n=3) | unknown |" in report
    assert "blocked (n=3) | unknown" in report


def test_incomplete_run_is_warned():
    manifest = {
        "run_id": "20260101T000000Z",
        "preset": 1,
        "target": "http://agent:8080",
        "model": "test",
        "git_commit": "abc",
        "runs": 1,
        "started_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-01T00:01:00Z",
    }
    report = render_run_md(manifest, [], title="Run Summary", complete=False)
    assert "latest.md" in report
    assert "Complete: `false`" in report
