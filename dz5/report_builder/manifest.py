from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MANIFEST_FIELDS = (
    "run_id",
    "started_at",
    "finished_at",
    "git_commit",
    "runner_version",
    "target",
    "preset",
    "runs",
    "model",
    "images",
    "scenario_set_sha256",
    "complete",
    "env",
)
RESULT_FIELDS = (
    "scenario",
    "preset",
    "run",
    "status",
    "verdict",
    "http_code",
    "latency_ms",
    "leak",
    "forbidden_tool",
    "db_changed",
    "error",
)
VERDICTS = {"blocked", "bypass", "mixed", "unknown", "error", "not_applicable"}


class ReportError(ValueError):
    pass


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportError(f"cannot read {path}: {exc}") from exc


def load_manifest(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "manifest.json"
    data = load_json(path)
    if not isinstance(data, dict):
        raise ReportError(f"{path} must contain a JSON object")
    missing = [field for field in MANIFEST_FIELDS if field not in data]
    if missing:
        raise ReportError(f"{path} is missing fields: {', '.join(missing)}")
    if not isinstance(data["run_id"], str) or not data["run_id"]:
        raise ReportError(f"{path}: run_id must be a non-empty string")
    if not isinstance(data["preset"], int) or data["preset"] not in range(1, 6):
        raise ReportError(f"{path}: preset must be 1..5")
    if not isinstance(data["runs"], int) or data["runs"] < 1:
        raise ReportError(f"{path}: runs must be a positive integer")
    if not isinstance(data["complete"], bool):
        raise ReportError(f"{path}: complete must be a boolean")
    if not isinstance(data["images"], dict):
        raise ReportError(f"{path}: images must be an object")
    if not isinstance(data["env"], dict):
        raise ReportError(f"{path}: env must be an object")
    return data


def load_results(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "results.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ReportError(f"cannot read {path}: {exc}") from exc
    results: list[dict[str, Any]] = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReportError(f"{path}:{number}: invalid JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise ReportError(f"{path}:{number}: result must be an object")
        missing = [field for field in RESULT_FIELDS if field not in row]
        if missing:
            raise ReportError(
                f"{path}:{number}: missing fields: {', '.join(missing)}"
            )
        if row["verdict"] not in VERDICTS:
            raise ReportError(f"{path}:{number}: invalid verdict {row['verdict']!r}")
        if not isinstance(row["scenario"], str) or not row["scenario"]:
            raise ReportError(f"{path}:{number}: invalid scenario")
        if not isinstance(row["run"], int) or row["run"] < 1:
            raise ReportError(f"{path}:{number}: invalid run")
        results.append(row)
    return results


def load_summary(run_dir: Path) -> list[dict[str, Any]] | None:
    path = run_dir / "summary.json"
    if not path.is_file():
        return None
    data = load_json(path)
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise ReportError(f"{path} must contain a list of objects")
    return data
