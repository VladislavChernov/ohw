from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from . import __version__
from .models import Scenario
from .scenarios import scenario_set_sha256

ENV_KEYS = (
    "ALLOW_WRITES",
    "BUILTIN_GUARDS",
    "EXTERNAL_GUARD",
    "BLOCK_ON_FAIL",
    "ALLOW_RUNTIME_TOGGLES",
    "OLLAMA_MODEL",
    "OLLAMA_MODEL_DIGEST",
    "PRESET",
    "TARGET",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def run_id_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def git_commit() -> str:
    configured = os.environ.get("GIT_COMMIT", "").strip()
    if configured:
        return configured
    if shutil.which("git") is None:
        return "unknown"
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def collect_images() -> dict[str, str]:
    images: dict[str, str] = {}
    for key, env_name in (
        ("agent", "AGENT_IMAGE"),
        ("agent-fixed", "AGENT_IMAGE_FIXED"),
        ("wall", "WALL_IMAGE"),
        ("ollama", "OLLAMA_IMAGE"),
    ):
        value = os.environ.get(env_name, "").strip()
        if value:
            images[key] = value
    return images


def relevant_env() -> dict[str, str]:
    result: dict[str, str] = {}
    for key in ENV_KEYS:
        value = os.environ.get(key)
        if value is not None and value.strip():
            result[key] = value.strip()
    return result


def build_manifest(
    *,
    run_id: str,
    started_at: str,
    finished_at: str,
    target: str,
    preset: int,
    runs: int,
    scenarios: Iterable[Scenario],
    complete: bool,
    augustus: dict[str, Any] | None,
) -> dict[str, Any]:
    scenario_list = list(scenarios)
    return {
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "git_commit": git_commit(),
        "runner_version": __version__,
        "target": target,
        "preset": preset,
        "runs": runs,
        "model": os.environ.get("OLLAMA_MODEL", "unknown"),
        "model_digest": os.environ.get("OLLAMA_MODEL_DIGEST", "unknown"),
        "images": collect_images(),
        "scenario_set_sha256": scenario_set_sha256(scenario_list),
        "scenario_ids": [scenario.id for scenario in scenario_list],
        "complete": complete,
        "env": relevant_env(),
        "augustus": augustus
        if augustus is not None
        else {"status": "skipped", "reason": "disabled"},
    }


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
