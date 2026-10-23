from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from .aggregate import summarize_results
from .client import AgentClient, StepResponse
from .db import DBClient, DBError
from .evaluators import Evaluation, evaluate
from .manifest import build_manifest, run_id_now, utc_now, write_json_atomic
from .models import Scenario

REDACTIONS = (
    (re.compile(r"sk-lab-[A-Za-z0-9-]+"), "<token>"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "<email>"),
    (re.compile(r"\+7 \(\d{3}\) \d{3}-\d{2}-\d{2}"), "<phone>"),
    (re.compile(r"\b\d{4} \d{6}\b"), "<passport>"),
)


@dataclass(frozen=True)
class RunOptions:
    target: str
    preset: int
    runs: int
    out: Path | None
    reports_dir: Path
    timeout: float
    restore_db: bool
    augustus: bool
    scenario_ids: tuple[str, ...]
    scenarios_dir: Path
    db_path: Path
    db_scripts_dir: Path


def redact(text: str) -> str:
    result = text
    for pattern, replacement in REDACTIONS:
        result = pattern.sub(replacement, result)
    return result


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    return value


def _step_detail(index: int, message: str, response: StepResponse) -> dict[str, Any]:
    return {
        "step": index,
        "request": {"message": message},
        "http_code": response.status_code,
        "latency_ms": response.latency_ms,
        "error": response.error,
        "body": _redact_value(response.body),
        "response_text": redact(response.text),
    }


def _not_applicable(scenario: Scenario, preset: int, run: int) -> dict[str, Any]:
    return {
        "scenario": scenario.id,
        "owasp": scenario.owasp,
        "title": scenario.title,
        "preset": preset,
        "run": run,
        "status": "completed",
        "verdict": "not_applicable",
        "http_code": None,
        "latency_ms": None,
        "leak": False,
        "forbidden_tool": False,
        "db_changed": None,
        "error": None,
    }


def _cleanup_error(
    teardown_error: str | None,
    restore_error: str | None,
) -> str | None:
    errors = [error for error in (teardown_error, restore_error) if error]
    if not errors:
        return None
    return "; ".join(errors)


def run_once(
    scenario: Scenario,
    *,
    client: AgentClient,
    db: DBClient,
    preset: int,
    run: int,
    restore_db: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    responses: list[StepResponse] = []
    steps: list[dict[str, Any]] = []
    db_before: dict[str, Any] | None = None
    db_after: dict[str, Any] | None = None
    setup_error: str | None = None
    teardown_error: str | None = None
    restore_error: str | None = None
    snapshot_error: str | None = None
    started = time.perf_counter()

    try:
        if restore_db and scenario.restore_db:
            db.restore()
        for statement in scenario.setup:
            db.execute(statement)
        try:
            db_before = db.snapshot()
        except DBError as exc:
            snapshot_error = str(exc)
        for index, message in enumerate(scenario.messages, start=1):
            response = client.chat(message)
            responses.append(response)
            steps.append(_step_detail(index, message, response))
            if response.error or response.status_code is None:
                break
            if response.status_code >= 500 or response.status_code not in {200, 400, 403}:
                break
            if response.status_code in {400, 403}:
                break
        try:
            db_after = db.snapshot()
        except DBError as exc:
            snapshot_error = snapshot_error or str(exc)
    except DBError as exc:
        setup_error = str(exc)
    finally:
        for statement in scenario.teardown:
            try:
                db.execute(statement)
            except DBError as exc:
                teardown_error = str(exc)
                break
        if restore_db and scenario.restore_db:
            try:
                db.restore()
            except DBError as exc:
                restore_error = str(exc)

    if setup_error:
        evaluation = Evaluation(
            verdict="error",
            status="error",
            http_code=None,
            latency_ms=None,
            leak=False,
            forbidden_tool=False,
            forbidden_sql=False,
            db_changed=None,
            error=setup_error,
        )
    else:
        evaluation = evaluate(scenario, responses, db_before, db_after)
        if snapshot_error and scenario.destructive:
            evaluation = Evaluation(
                **{
                    **evaluation.__dict__,
                    "verdict": "error",
                    "status": "error",
                    "error": evaluation.error or snapshot_error,
                }
            )

    cleanup_error = _cleanup_error(teardown_error, restore_error)
    if cleanup_error:
        evaluation = Evaluation(
            **{
                **evaluation.__dict__,
                "error": f"{evaluation.error}; {cleanup_error}"
                if evaluation.error
                else cleanup_error,
            }
        )
    elapsed = round((time.perf_counter() - started) * 1000)
    result = {
        "scenario": scenario.id,
        "owasp": scenario.owasp,
        "title": scenario.title,
        "preset": preset,
        "run": run,
        "status": evaluation.status,
        "verdict": evaluation.verdict,
        "http_code": evaluation.http_code,
        "latency_ms": evaluation.latency_ms,
        "leak": evaluation.leak,
        "forbidden_tool": evaluation.forbidden_tool,
        "db_changed": evaluation.db_changed,
        "error": evaluation.error,
        "cleanup_error": cleanup_error,
        "steps": len(steps),
        "tool_calls": len(evaluation.tool_calls),
    }
    details = {
        "scenario": scenario.id,
        "owasp": scenario.owasp,
        "title": scenario.title,
        "preset": preset,
        "run": run,
        "started_at": utc_now(),
        "elapsed_ms": elapsed,
        "request_count": len(scenario.messages),
        "steps": steps,
        "evaluation": {
            "verdict": evaluation.verdict,
            "status": evaluation.status,
            "leak": evaluation.leak,
            "forbidden_tool": evaluation.forbidden_tool,
            "forbidden_sql": evaluation.forbidden_sql,
            "db_changed": evaluation.db_changed,
            "error": evaluation.error,
            "matched_leaks": evaluation.matched_leaks,
            "matched_sql": evaluation.matched_sql,
            "tool_calls": evaluation.tool_calls,
        },
        "db_before": db_before,
        "db_after": db_after,
        "snapshot_error": snapshot_error,
        "setup_error": setup_error,
        "teardown_error": teardown_error,
        "restore_error": restore_error,
    }
    return result, details


DEFAULT_AUGUSTUS_PROBES = (
    "dan.DAN_Jailbreak",
    "latentinjection.LatentJailbreak",
    "goodside.SystemPromptConfusion",
)


def _augustus_scan_config(
    source: Path,
    target: str,
    output_path: Path,
) -> dict[str, Any]:
    with source.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    if not isinstance(config, dict):
        raise ValueError("augustus config must be a YAML mapping")

    generators = config.setdefault("generators", {})
    if not isinstance(generators, dict):
        raise ValueError("augustus generators must be a mapping")
    generator = generators.setdefault("rest.Rest", {})
    if not isinstance(generator, dict):
        raise ValueError("augustus generators.rest.Rest must be a mapping")

    generator["uri"] = f"{target.rstrip('/')}/chat"
    generator.setdefault("method", "POST")
    generator.setdefault(
        "req_template_json_object",
        {"message": "$INPUT"},
    )
    generator.setdefault("response_json", True)
    generator.setdefault("response_json_field", "$.reply")

    output = config.setdefault("output", {})
    if not isinstance(output, dict):
        raise ValueError("augustus output must be a mapping")
    output["format"] = "jsonl"
    output["path"] = str(output_path)
    return config


def _augustus_probes() -> list[str]:
    raw = os.environ.get("AUGUSTUS_PROBES", "")
    values = raw.split(",") if raw else DEFAULT_AUGUSTUS_PROBES
    return [value.strip() for value in values if value.strip()]


def _augustus_command(
    binary: str,
    config_path: Path,
    results_path: Path,
    report_path: Path,
    probes: list[str],
) -> list[str]:
    command = [
        binary,
        "scan",
        "rest.Rest",
        "--config-file",
        str(config_path),
        "--format",
        "jsonl",
        "--output",
        str(results_path),
        "--html",
        str(report_path),
    ]
    for probe in probes:
        command.extend(["--probe", probe])
    return command


def run_augustus(
    enabled: bool,
    out_dir: Path,
    target: str,
    db: DBClient | None = None,
) -> dict[str, Any]:
    if not enabled:
        return {"status": "skipped", "reason": "disabled"}
    binary = shutil.which("augustus")
    if binary is None:
        return {"status": "skipped", "reason": "augustus binary not found"}

    source = Path(
        os.environ.get(
            "AUGUSTUS_CONFIG",
            "/app/scripts/augustus/augustus.config.yaml",
        )
    )
    output_dir = out_dir / "augustus"
    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "results.jsonl"
    report_path = output_dir / "report.html"
    config_path = output_dir / "config.yaml"

    try:
        scan_config = _augustus_scan_config(source, target, results_path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        return {"status": "error", "reason": f"config: {exc}"}

    config_path.write_text(
        yaml.safe_dump(scan_config, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    probes = _augustus_probes()
    command = _augustus_command(
        binary,
        config_path,
        results_path,
        report_path,
        probes,
    )

    db_before: dict[str, Any] | None = None
    db_after: dict[str, Any] | None = None
    db_error: str | None = None
    db_changed: bool | None = None
    if db is not None:
        try:
            db_before = db.snapshot()
        except DBError as exc:
            db_error = str(exc)

    try:
        completed = subprocess.run(
            command,
            cwd=out_dir,
            check=False,
            capture_output=True,
            text=True,
            timeout=1800,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "error", "reason": str(exc), "probes": probes}
    finally:
        if db is not None:
            try:
                db_after = db.snapshot()
            except DBError as exc:
                db_error = db_error or str(exc)
            if db_before is not None and db_after is not None:
                db_changed = db_before != db_after

    (output_dir / "stdout.txt").write_text(completed.stdout, encoding="utf-8")
    (output_dir / "stderr.txt").write_text(completed.stderr, encoding="utf-8")
    if db_before is not None:
        write_json_atomic(output_dir / "db-before.json", db_before)
    if db_after is not None:
        write_json_atomic(output_dir / "db-after.json", db_after)

    status = "completed" if completed.returncode == 0 else "error"
    if status == "completed" and not results_path.is_file():
        status = "error"
    return {
        "status": status,
        "returncode": completed.returncode,
        "target": target,
        "probes": probes,
        "output": str(results_path.relative_to(out_dir)),
        "report": str(report_path.relative_to(out_dir)),
        "db_changed": db_changed,
        "db_error": db_error,
    }


def _select_out_dir(options: RunOptions, run_id: str) -> Path:
    if options.out is not None:
        return options.out
    return options.reports_dir / run_id


def execute(options: RunOptions, scenarios: Iterable[Scenario]) -> Path:
    scenario_list = list(scenarios)
    run_id = run_id_now()
    out_dir = _select_out_dir(options, run_id)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise FileExistsError(f"output directory is not empty: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    details_dir = out_dir / "details"
    details_dir.mkdir(parents=True, exist_ok=True)

    started_at = utc_now()
    client = AgentClient(options.target, timeout=options.timeout)
    db = DBClient(options.db_path, options.db_scripts_dir)
    results: list[dict[str, Any]] = []

    results_path = out_dir / "results.jsonl"
    with results_path.open("w", encoding="utf-8") as results_file:
        for scenario in scenario_list:
            for run in range(1, options.runs + 1):
                if not scenario.applies_to(options.preset):
                    result = _not_applicable(scenario, options.preset, run)
                    details = {
                        "scenario": scenario.id,
                        "preset": options.preset,
                        "run": run,
                        "evaluation": {"verdict": "not_applicable"},
                    }
                else:
                    result, details = run_once(
                        scenario,
                        client=client,
                        db=db,
                        preset=options.preset,
                        run=run,
                        restore_db=options.restore_db,
                    )
                results.append(result)
                results_file.write(json.dumps(result, ensure_ascii=False) + "\n")
                results_file.flush()
                scenario_dir = details_dir / scenario.id
                scenario_dir.mkdir(parents=True, exist_ok=True)
                write_json_atomic(scenario_dir / f"run-{run}.json", details)

    augustus = run_augustus(options.augustus, out_dir, options.target, db=db)
    completed = len(results) == len(scenario_list) * options.runs
    has_error = any(
        result.get("verdict") == "error" or result.get("cleanup_error")
        for result in results
    )
    selected_subset = bool(options.scenario_ids)
    complete = (
        options.runs >= 3
        and completed
        and not has_error
        and not selected_subset
    )
    finished_at = utc_now()
    manifest = build_manifest(
        run_id=run_id,
        started_at=started_at,
        finished_at=finished_at,
        target=client.target,
        preset=options.preset,
        runs=options.runs,
        scenarios=scenario_list,
        complete=complete,
        augustus=augustus,
    )
    write_json_atomic(out_dir / "manifest.json", manifest)
    write_json_atomic(out_dir / "summary.json", summarize_results(results))
    return out_dir
