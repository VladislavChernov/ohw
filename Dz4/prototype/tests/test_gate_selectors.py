"""Селектор гейта не должен молчать.

Если путь в `infra/gate_selectors.yaml` перестал существовать, селектор начнёт
отбирать пустое множество тестов и выдавать зелёный результат, ни разу не проверив
ничего. Это ровно тот класс отказа, который гейт обязан ловить, поэтому он закрыт
тестом, а не замечанием в документации.
"""

from __future__ import annotations

import fnmatch
from pathlib import Path

import pytest
import yaml

PROTO = Path(__file__).resolve().parents[1]
SELECTORS = PROTO / "infra" / "gate_selectors.yaml"


def _spec() -> dict:
    return yaml.safe_load(SELECTORS.read_text(encoding="utf-8"))


def test_selectors_file_exists() -> None:
    assert SELECTORS.is_file(), f"селектор гейта отсутствует: {SELECTORS}"


def test_every_declared_test_path_exists() -> None:
    areas = _spec()["areas"]
    assert areas, "в селекторе нет ни одной области"
    missing = [
        rel
        for spec in areas.values()
        for rel in spec["tests"]
        if not (PROTO / rel).is_file()
    ]
    assert not missing, f"в селекторе пути, которых нет на диске: {missing}"


def test_every_area_declares_match_and_tests() -> None:
    areas = _spec()["areas"]
    empty = [name for name, spec in areas.items() if not spec.get("match") or not spec.get("tests")]
    assert not empty, f"области без match или tests: {empty}"


def test_gate_script_declares_full_and_changed_modes() -> None:
    script = (PROTO / "infra" / "scripts" / "gate.sh").read_text(encoding="utf-8")
    assert "full)" in script, "в gate.sh нет режима full"
    assert "changed" in script, "в gate.sh нет режима changed"


def test_gate_script_does_not_inherit_image_pythonpath() -> None:
    """Образ задаёт `PYTHONPATH=/app/src` - на код, запечённый в образ.

    Он попадает в `sys.path` раньше `.pth` из editable-установки, поэтому гейт молча
    проверяет старый снимок вместо рабочей копии и при этом остаётся зелёным. Скрипт
    обязан переопределять путь явно.

    Комментарии не считаются: объяснять ловушку нужно, а зависеть от неё - нет.
    """
    script = (PROTO / "infra" / "scripts" / "gate.sh").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in script.splitlines() if not line.lstrip().startswith("#")
    )
    assert "PYTHONPATH=" in code, "gate.sh не переопределяет PYTHONPATH"
    assert "/app/src" not in code, "gate.sh берёт код, запечённый в образ, а не рабочую копию"


def test_unknown_changed_file_is_not_silently_skipped() -> None:
    """Файл вне описанных областей обязан останавливать гейт, а не проходить мимо."""
    spec = _spec()
    patterns = [str(p) for area in spec["areas"].values() for p in area["match"]]
    assert patterns, "в селекторе нет ни одного match-шаблона"
    unclassified = "Dz4/prototype/src/graphrag_proto/example_module_that_is_not_covered.py"
    assert not any(fnmatch.fnmatch(unclassified, p) for p in patterns)


def test_match_patterns_are_relative_to_git_root() -> None:
    """`git status --porcelain` отдаёт пути от корня репозитория, а не от `prototype/`.

    Обрезать префикс на границе нельзя: тогда правило сопоставления живёт в двух
    местах, и одно из них со временем разойдётся.
    """
    areas = _spec()["areas"]
    assert areas, "в селекторе нет ни одной области"
    wrong = [
        pattern
        for spec in areas.values()
        for pattern in spec["match"]
        if str(pattern).startswith("prototype/") or str(pattern).startswith("tests/")
    ]
    assert not wrong, f"match-шаблоны без корня репозитория: {wrong}"


@pytest.mark.parametrize("script_name", ["gate.sh"])
def test_gate_script_is_executable_format(script_name: str) -> None:
    text = (PROTO / "infra" / "scripts" / script_name).read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh"), f"{script_name} без шебанга"
    assert "\r\n" not in text, f"{script_name} с CRLF: скрипт не переживёт исполнение в контейнере"