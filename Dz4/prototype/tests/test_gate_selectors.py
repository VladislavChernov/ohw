"""Селектор гейта не должен молчать.

Если путь в `infra/gate_selectors.yaml` перестал существовать, селектор начнёт
отбирать пустое множество тестов и выдавать зелёный результат, ни разу не проверив
ничего. Это ровно тот класс отказа, который гейт обязан ловить, поэтому он закрыт
тестом, а не замечанием в документации.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PROTO = Path(__file__).resolve().parents[1]
SELECTORS = PROTO / "infra" / "gate_selectors.yaml"
CLASSIFIER = PROTO / "infra" / "checks" / "gate_changed_areas.py"
_UNKNOWN_EXIT = 3


def _spec() -> dict:
    return yaml.safe_load(SELECTORS.read_text(encoding="utf-8"))


def _classify_exit(*paths: str) -> int:
    """Прогнать классификатор так же, как это делает `gate.sh`, и вернуть код возврата."""
    result = subprocess.run(
        [sys.executable, str(CLASSIFIER), "--selectors", str(SELECTORS), *paths],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode


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


def test_unclassified_path_exits_with_demand_for_full() -> None:
    """Контракт, а не ширина шаблонов: файл вне областей обязан вернуть код 3.

    Проверка «ни один шаблон не слишком широкий» этот контракт не защищает: её проходит
    и классификатор, который на неизвестный файл печатает пустую строку и выходит 0.
    """
    unknown = "Dz4/prototype/src/graphrag_proto/example_module_that_is_not_covered.py"
    known = "Dz4/docs/05_adr_log.md"

    assert _classify_exit(unknown) == _UNKNOWN_EXIT, "неизвестный файл прошёл молча"
    assert _classify_exit(known) == 0, "известный файл не классифицировался"


def test_empty_path_list_is_not_treated_as_nothing_to_check() -> None:
    """Пустой список - это «проверять нечего», и гейт обязан это сказать, а не замолчать."""
    assert _classify_exit("") == _UNKNOWN_EXIT


def test_selector_covers_the_modules_it_claims() -> None:
    """Область адаптеров обязана называть контрактные тесты каждого перечисленного модуля.

    Область `adapters` матчит весь пакет, поэтому тест `llm.py` обязан попадать в её
    список: иначе правка контракта LLM прогоняет адаптеры и не прогоняет его тест.
    """
    areas = _spec()["areas"]
    tests = {Path(rel).name for rel in areas["adapters"]["tests"]}
    expected = {
        "test_factory_adapters.py",
        "test_adapter_catalog_ssot.py",
        "test_retrieval_adapters.py",
        "test_bge_adapters.py",
        "test_llm_openai_adapter.py",
    }
    missing = expected - tests
    assert not missing, f"в области adapters нет контрактных тестов: {sorted(missing)}"


def test_declared_stubs_match_the_implementation() -> None:
    """`deterministic` и `noop` названы заглушками - это должно быть верно и в коде.

    Заглушки определяются по признаку: псевдо-вектор фиксированной размерности и rerank,
    отдающий `score` как есть. Если они когда-нибудь станут настоящими, тест упадёт.
    """
    from graphrag_proto.retrieval.adapters.deterministic import DEFAULT_DIM
    from graphrag_proto.retrieval.adapters.llm import FakeLLM
    from graphrag_proto.retrieval.adapters.reranker import NoOpRerankerAdapter

    assert DEFAULT_DIM < 1024, f"deterministic стал настоящим: {DEFAULT_DIM} измерений"
    chunks = [{"score": 0.9}, {"score": 0.1}]
    assert NoOpRerankerAdapter().rerank("q", chunks) == [0.9, 0.1], "noop что-то делает"
    assert FakeLLM is not None


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


@pytest.mark.parametrize("script_name", ["gate.sh", "lint.sh", "diff_check.sh"])
def test_gate_script_is_posix_shell_source(script_name: str) -> None:
    text = (PROTO / "infra" / "scripts" / script_name).read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh"), f"{script_name} без шебанга"
    assert "\r\n" not in text, f"{script_name} с CRLF: скрипт не переживёт исполнение в контейнере"


def test_gate_script_reports_failure_on_empty_file_list() -> None:
    """Ранний выход «изменённых файлов нет» обязан нести накопленный результат.

    Иначе гейт отвечает 0 при упавшем `ruff` - ровно тот молчаливый зелёный, ради
    закрытия которого инструмент и написан.
    """
    script = (PROTO / "infra" / "scripts" / "gate.sh").read_text(encoding="utf-8")
    early_exit = 'echo "=== pytest: изменённых файлов нет ==="; exit "$FAILED"'
    assert early_exit in script, "ранний выход теряет код возврата"