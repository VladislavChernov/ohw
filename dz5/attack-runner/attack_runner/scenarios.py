from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import yaml

from .models import Expectation, Scenario


class ScenarioError(ValueError):
    pass


def _as_tuple(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ScenarioError(f"{field} must be a string or a list of strings")
    return tuple(value)


def _as_int_tuple(value: Any, field: str) -> tuple[int, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ScenarioError(f"{field} must be a list")
    try:
        return tuple(int(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise ScenarioError(f"{field} must contain integers") from exc


def _required_text(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ScenarioError(f"{key} is required and must be a non-empty string")
    return value.strip()


def _expectation(data: dict[str, Any]) -> Expectation:
    if not isinstance(data, dict):
        raise ScenarioError("expect is required and must be a mapping")
    return Expectation(
        leak_markers=_as_tuple(data.get("leak_markers"), "leak_markers"),
        leak_regex=_as_tuple(data.get("leak_regex"), "leak_regex"),
        forbidden_tools=_as_tuple(data.get("forbidden_tools"), "forbidden_tools"),
        forbidden_sql=_as_tuple(data.get("forbidden_sql"), "forbidden_sql"),
        allowed_tool_prefixes=_as_tuple(
            data.get("allowed_tool_prefixes"), "allowed_tool_prefixes"
        ),
        refusal_markers=_as_tuple(data.get("refusal_markers"), "refusal_markers"),
    )


def scenario_from_dict(data: dict[str, Any], source: str = "") -> Scenario:
    if not isinstance(data, dict):
        raise ScenarioError("scenario root must be a mapping")
    scenario_id = _required_text(data, "id")
    if not all(ch.isalnum() or ch in "-_" for ch in scenario_id):
        raise ScenarioError(f"invalid scenario id: {scenario_id}")
    destructive = bool(data.get("destructive", False))
    restore_db = bool(data.get("restore_db", destructive))
    return Scenario(
        id=scenario_id,
        owasp=_required_text(data, "owasp"),
        title=_required_text(data, "title"),
        prompt=_required_text(data, "prompt"),
        expect=_expectation(data.get("expect")),
        steps=_as_tuple(data.get("steps"), "steps"),
        setup=_as_tuple(data.get("setup"), "setup"),
        teardown=_as_tuple(data.get("teardown"), "teardown"),
        destructive=destructive,
        restore_db=restore_db,
        applicable_presets=_as_int_tuple(
            data.get("applicable_presets"), "applicable_presets"
        ),
        source=source,
    )


def load_scenario(path: Path) -> Scenario:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ScenarioError(f"cannot load {path}: {exc}") from exc
    return scenario_from_dict(raw, source=str(path))


def load_scenarios(directory: Path) -> list[Scenario]:
    if not directory.is_dir():
        raise ScenarioError(f"scenarios directory not found: {directory}")
    scenarios = [load_scenario(path) for path in sorted(directory.glob("*.yaml"))]
    if not scenarios:
        raise ScenarioError(f"no YAML scenarios found in {directory}")
    ids = [scenario.id for scenario in scenarios]
    duplicates = sorted({scenario_id for scenario_id in ids if ids.count(scenario_id) > 1})
    if duplicates:
        raise ScenarioError(f"duplicate scenario ids: {', '.join(duplicates)}")
    return scenarios


def select_scenarios(
    scenarios: Iterable[Scenario], requested: Iterable[str]
) -> list[Scenario]:
    selected_ids = list(requested)
    if not selected_ids:
        return list(scenarios)
    by_id = {scenario.id: scenario for scenario in scenarios}
    missing = [scenario_id for scenario_id in selected_ids if scenario_id not in by_id]
    if missing:
        raise ScenarioError(f"unknown scenario ids: {', '.join(missing)}")
    requested_set = set(selected_ids)
    return [scenario for scenario in scenarios if scenario.id in requested_set]


def scenario_set_sha256(scenarios: Iterable[Scenario]) -> str:
    canonical = [
        {
            "id": scenario.id,
            "owasp": scenario.owasp,
            "title": scenario.title,
            "prompt": scenario.prompt,
            "steps": list(scenario.steps),
            "setup": list(scenario.setup),
            "teardown": list(scenario.teardown),
            "destructive": scenario.destructive,
            "restore_db": scenario.restore_db,
            "expect": {
                "leak_markers": list(scenario.expect.leak_markers),
                "leak_regex": list(scenario.expect.leak_regex),
                "forbidden_tools": list(scenario.expect.forbidden_tools),
                "forbidden_sql": list(scenario.expect.forbidden_sql),
                "allowed_tool_prefixes": list(scenario.expect.allowed_tool_prefixes),
                "refusal_markers": list(scenario.expect.refusal_markers),
            },
        }
        for scenario in sorted(scenarios, key=lambda item: item.id)
    ]
    payload = json.dumps(canonical, ensure_ascii=True, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
