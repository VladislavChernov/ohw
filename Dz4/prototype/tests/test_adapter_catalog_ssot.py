"""SSOT-контракт YAML↔фабрика: значения namespace `adapters` ∈ ADAPTER_CATALOG.

Закрывает класс дрейфа (review.md, review_2 §4.1, critical_review №1, fast_review2
P0-1): `infra/config/adapters.yaml` и `namespaces.yaml` декларировали id
(`neo4j_graph`, `neo4j_vector`, `ollama`), которых нет в каталоге фабрики, — не
существовало одного словаря, который бы выбрал «что активно сейчас».
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from graphrag_proto.retrieval.adapters.factory import (
    ADAPTER_CATALOG,
    _build_graph,
    _build_vector,
)

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "infra" / "config"
_ADAPTERS_FILES = ("adapters.yaml", "namespaces.yaml")
_SLOT_NAMES = ("graph_store", "vector_store", "embeddings", "reranker", "llm")

# A-2 (ADR-024): ключ-контейнер capability-деклараций в namespace `adapters` — НЕ слот.
_CAPABILITIES_KEY = "capabilities"
_CAPABILITIES_SLOTS = ("graph_store", "vector_store")


def _adapters_namespace(path: Path) -> Mapping[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data["adapters"]


def _slots(ns: Mapping[str, Any]) -> Any:
    for slot, provider in ns.items():
        if slot == _CAPABILITIES_KEY:
            continue
        yield slot, provider


def test_yaml_adapter_ids_in_catalog() -> None:
    for filename in _ADAPTERS_FILES:
        ns = _adapters_namespace(_CONFIG_DIR / filename)
        for slot, provider in _slots(ns):
            assert slot in ADAPTER_CATALOG, f"{filename}: слот {slot!r} отсутствует в каталоге"
            assert provider in ADAPTER_CATALOG[slot], (
                f"{filename}: {slot}={provider!r} не в каталоге {ADAPTER_CATALOG[slot]}"
            )


def test_yaml_covers_all_catalog_slots() -> None:
    yaml_slots: set[str] = set()
    for filename in _ADAPTERS_FILES:
        yaml_slots |= {slot for slot, _ in _slots(_adapters_namespace(_CONFIG_DIR / filename))}
    assert yaml_slots == set(ADAPTER_CATALOG), (
        f"набор слотов YAML {sorted(yaml_slots)} != каталог {sorted(ADAPTER_CATALOG)}"
    )


def test_slot_invariants() -> None:
    for slot in _SLOT_NAMES:
        assert slot in ADAPTER_CATALOG
        assert ADAPTER_CATALOG[slot], f"{slot}: каталог не должен быть пустым"


def test_declared_capabilities_are_valid_and_covered() -> None:
    """A-2: декларация capability есть для каждой оси и значение ∈ {atomic, best_effort}."""
    for filename in _ADAPTERS_FILES:
        ns = _adapters_namespace(_CONFIG_DIR / filename)
        caps = ns.get(_CAPABILITIES_KEY)
        assert isinstance(caps, Mapping), f"{filename}: отсутствует слот capabilities"
        assert set(caps) == set(_CAPABILITIES_SLOTS), (
            f"{filename}: capabilities покрывают {sorted(caps)}, ожидалось {sorted(_CAPABILITIES_SLOTS)}"
        )
        for slot, value in caps.items():
            assert value in ("atomic", "best_effort"), (
                f"{filename}: {slot}={value!r} не в {{atomic, best_effort}}"
            )


def test_declared_capabilities_match_implementation() -> None:
    """A-2: декларация YAML совпадает с фактическим `consistency_capability()`.

    Ядро доверяет декларации, поэтому ССОТ обязан держать её в синхроне с реализацией
    (тот же класс дрейфа, что закрыт тестом слотов).
    """
    for filename in _ADAPTERS_FILES:
        ns = _adapters_namespace(_CONFIG_DIR / filename)
        caps = ns[_CAPABILITIES_KEY]
        graph = _build_graph(ns["graph_store"])
        vector = _build_vector(ns["vector_store"])
        assert graph.consistency_capability() == caps["graph_store"], filename
        assert vector.consistency_capability() == caps["vector_store"], filename


def test_declared_atomic_pair_is_genuine() -> None:
    """A-2 (ADR-024): декларированная atomic-пара — действительно атомарная.

    «atomic» обеих осей обязано означать общий движок (`engine_key` совпадает): иначе
    ядро по `_is_atomic_pair` запишет оси best_effort, а оператор рассчитывает на
    атомарность по YAML (класс дрейфа среди пары, а не оси).
    """
    for filename in _ADAPTERS_FILES:
        ns = _adapters_namespace(_CONFIG_DIR / filename)
        caps = ns[_CAPABILITIES_KEY]
        if caps["graph_store"] != "atomic" or caps["vector_store"] != "atomic":
            continue
        graph = _build_graph(ns["graph_store"])
        vector = _build_vector(ns["vector_store"])
        assert graph.engine_key() is not None, filename
        assert vector.engine_key() is not None, filename
        assert graph.engine_key() == vector.engine_key(), (
            f"{filename}: оси заявлены atomic, но engine_key различаются "
            f"({graph.engine_key()} vs {vector.engine_key()}) — реальная пара best_effort"
        )