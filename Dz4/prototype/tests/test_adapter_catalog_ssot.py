"""SSOT-контракт YAML↔фабрика: значения namespace `adapters` ∈ ADAPTER_CATALOG.

Закрывает класс дрейфа (review.md, review_2 §4.1, critical_review №1, fast_review2
P0-1): `infra/config/adapters.yaml` и `namespaces.yaml` декларировали id
(`neo4j_graph`, `neo4j_vector`, `ollama`), которых нет в каталоге фабрики, — не
существовало одного словаря, который бы выбрал «что активно сейчас».
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import yaml

from graphrag_proto.retrieval.adapters.factory import ADAPTER_CATALOG

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "infra" / "config"
_ADAPTERS_FILES = ("adapters.yaml", "namespaces.yaml")
_SLOT_NAMES = ("graph_store", "vector_store", "embeddings", "reranker", "llm")


def _adapters_namespace(path: Path) -> Mapping[str, str]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data["adapters"]


def test_yaml_adapter_ids_in_catalog() -> None:
    for filename in _ADAPTERS_FILES:
        ns = _adapters_namespace(_CONFIG_DIR / filename)
        for slot, provider in ns.items():
            assert slot in ADAPTER_CATALOG, f"{filename}: слот {slot!r} отсутствует в каталоге"
            assert provider in ADAPTER_CATALOG[slot], (
                f"{filename}: {slot}={provider!r} не в каталоге {ADAPTER_CATALOG[slot]}"
            )


def test_yaml_covers_all_catalog_slots() -> None:
    yaml_slots: set[str] = set()
    for filename in _ADAPTERS_FILES:
        yaml_slots |= set(_adapters_namespace(_CONFIG_DIR / filename))
    assert yaml_slots == set(ADAPTER_CATALOG), (
        f"набор слотов YAML {sorted(yaml_slots)} != каталог {sorted(ADAPTER_CATALOG)}"
    )


def test_slot_invariants() -> None:
    for slot in _SLOT_NAMES:
        assert slot in ADAPTER_CATALOG
        assert ADAPTER_CATALOG[slot], f"{slot}: каталог не должен быть пустым"