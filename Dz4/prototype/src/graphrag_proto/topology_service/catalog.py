"""Каталог реализованных провайдеров (add-topology-adapters).

Единый источник — регистр фабрики `retrieval/adapters/factory.py`
(`ADAPTER_CATALOG`); здесь — только удобное API для Topology (:8005),
чтобы `available`/валидация PUT совпадали с тем, что реально умеет сборка.
"""

from __future__ import annotations

from graphrag_proto.retrieval.adapters.factory import ADAPTER_CATALOG

SLOT_ORDER: tuple[str, ...] = ("graph_store", "vector_store", "embeddings", "reranker", "llm")


def available_providers() -> dict[str, list[str]]:
    """Слот -> реализованные провайдеры (buildable, не прожект-SSOT)."""
    return {slot: list(ADAPTER_CATALOG[slot]) for slot in SLOT_ORDER}


def is_known(slot: str, provider: str) -> bool:
    providers = ADAPTER_CATALOG.get(slot)
    if not providers:
        return False
    return provider.strip().lower() in providers