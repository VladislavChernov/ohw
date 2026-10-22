import json
from pathlib import Path

from report_builder.__main__ import main


def write_run(run_dir: Path, *, complete: bool = True, verdict: str = "blocked") -> None:
    run_dir.mkdir(parents=True)
    manifest = {
        "run_id": run_dir.name,
        "started_at": "2026-01-01T00:00:00Z",
        "finished_at": "2026-01-01T00:01:00Z",
        "git_commit": "abc",
        "runner_version": "0.1.0",
        "target": "http://agent:8080",
        "preset": 1,
        "runs": 3,
        "model": "test",
        "images": {},
        "scenario_set_sha256": "a" * 64,
        "scenario_ids": ["llm01-ignore-previous"],
        "complete": complete,
        "env": {},
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    rows = [
        {
            "scenario": "llm01-ignore-previous",
            "owasp": "LLM01",
            "title": "Instruction override",
            "preset": 1,
            "run": run,
            "status": "completed",
            "verdict": verdict,
            "http_code": 403 if verdict == "blocked" else 200,
            "latency_ms": 100,
            "leak": verdict == "bypass",
            "forbidden_tool": False,
            "db_changed": False,
            "error": None,
        }
        for run in range(1, 4)
    ]
    (run_dir / "results.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )


def test_build_complete_writes_latest_and_red_team(tmp_path):
    run_dir = tmp_path / "20260101T000000Z"
    write_run(run_dir)
    assert main(["build", "--run", str(run_dir)]) == 0
    assert (run_dir / "summary.json").is_file()
    assert (run_dir / "summary.md").is_file()
    assert (tmp_path / "latest.md").is_file()
    assert "blocked (n=3)" in (tmp_path / "red-team.md").read_text(encoding="utf-8")


def test_incomplete_does_not_replace_latest(tmp_path):
    complete_run = tmp_path / "20260101T000000Z"
    write_run(complete_run)
    assert main(["build", "--run", str(complete_run)]) == 0
    before = (tmp_path / "latest.md").read_text(encoding="utf-8")

    incomplete_run = tmp_path / "20260102T000000Z"
    write_run(incomplete_run, complete=False, verdict="bypass")
    assert main(["build", "--run", str(incomplete_run)]) == 0
    assert (tmp_path / "latest.md").read_text(encoding="utf-8") == before
