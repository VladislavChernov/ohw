"""Фабрика адаптеров retrieval-контура (M2+topology, L1-02/L1-03).

Выбор транспорта — конфигурация, не код ядра. Два источника:
- env (M2): `GRAPH_STORE`, `VECTOR_STORE`, `EMBEDDER`, `RERANKER`, `LLM_ADAPTER`;
- карта адаптеров из Topology Orchestrator (M3, add-topology-adapters): явные слоты
  карты перекрывают env на уровне выбора провайдера («переключение без рестарта»).

Параметры соединений всегда из env (не зависит от карты провайдеров):
`NEO4J_URI/NEO4J_USER/NEO4J_PASSWORD`, `LLM_BASE_URL/LLM_MODEL/LLM_TEMPERATURE/LLM_MAX_TOKENS`.
Слоты и каталог провайдеров — SSOT `infra/config/namespaces.yaml` (namespace `adapters`).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from graphrag_proto.retrieval.adapters.base import (
    Embedder,
    GraphStoreProvider,
    LLMInference,
    Reranker,
    VectorStoreProvider,
)
from graphrag_proto.retrieval.adapters.deterministic import DeterministicEmbedder
from graphrag_proto.retrieval.adapters.inmemory import InMemoryGraphStore, InMemoryVectorStore
from graphrag_proto.retrieval.adapters.llm import FakeLLM, OpenAICompatibleAdapter
from graphrag_proto.retrieval.adapters.neo4j import Neo4jGraphStore, Neo4jVectorStore
from graphrag_proto.retrieval.adapters.reranker import NoOpRerankerAdapter

SLOT_GRAPH_STORE = "graph_store"
SLOT_VECTOR_STORE = "vector_store"
SLOT_EMBEDDINGS = "embeddings"
SLOT_RERANKER = "reranker"
SLOT_LLM = "llm"

ADAPTER_CATALOG: dict[str, list[str]] = {
    SLOT_GRAPH_STORE: ["neo4j", "inmemory"],
    SLOT_VECTOR_STORE: ["neo4j", "inmemory"],
    SLOT_EMBEDDINGS: ["deterministic"],
    SLOT_RERANKER: ["noop"],
    SLOT_LLM: ["openai", "fake"],
}

_DEFAULT_ADAPTERS: dict[str, str] = {
    SLOT_GRAPH_STORE: "inmemory",
    SLOT_VECTOR_STORE: "inmemory",
    SLOT_EMBEDDINGS: "deterministic",
    SLOT_RERANKER: "noop",
    SLOT_LLM: "openai",
}

_ENV_KEYS: dict[str, str] = {
    SLOT_GRAPH_STORE: "GRAPH_STORE",
    SLOT_VECTOR_STORE: "VECTOR_STORE",
    SLOT_EMBEDDINGS: "EMBEDDER",
    SLOT_RERANKER: "RERANKER",
    SLOT_LLM: "LLM_ADAPTER",
}


@dataclass
class Adapters:
    """Собранный набор провайдеров retrieval-контура."""

    graph_store: GraphStoreProvider
    vector_store: VectorStoreProvider
    embedder: Embedder
    reranker: Reranker
    llm: LLMInference


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _build_graph(kind: str) -> GraphStoreProvider:
    kind = kind.strip().lower()
    if kind == "inmemory":
        return InMemoryGraphStore()
    if kind == "neo4j":
        return Neo4jGraphStore(
            uri=_env("NEO4J_URI", "bolt://neo4j:7687"),
            user=_env("NEO4J_USER", "neo4j"),
            password=_env("NEO4J_PASSWORD", "graphrag"),
        )
    raise ValueError(f"graph_store={kind!r}: допустимо {', '.join(ADAPTER_CATALOG[SLOT_GRAPH_STORE])}")


def _build_vector(kind: str) -> VectorStoreProvider:
    kind = kind.strip().lower()
    if kind == "inmemory":
        return InMemoryVectorStore()
    if kind == "neo4j":
        return Neo4jVectorStore(
            uri=_env("NEO4J_URI", "bolt://neo4j:7687"),
            user=_env("NEO4J_USER", "neo4j"),
            password=_env("NEO4J_PASSWORD", "graphrag"),
        )
    raise ValueError(f"vector_store={kind!r}: допустимо {', '.join(ADAPTER_CATALOG[SLOT_VECTOR_STORE])}")


def _build_embedder(kind: str) -> Embedder:
    kind = kind.strip().lower()
    if kind == "deterministic":
        return DeterministicEmbedder()
    raise ValueError(f"embeddings={kind!r}: допустимо {', '.join(ADAPTER_CATALOG[SLOT_EMBEDDINGS])}")


def _build_reranker(kind: str) -> Reranker:
    kind = kind.strip().lower()
    if kind in ("noop", "none", "disabled", ""):
        return NoOpRerankerAdapter()
    raise ValueError(f"reranker={kind!r}: допустимо {', '.join(ADAPTER_CATALOG[SLOT_RERANKER])}")


def _build_llm(kind: str) -> LLMInference:
    kind = kind.strip().lower()
    if kind == "fake":
        return FakeLLM()
    if kind == "openai":
        return OpenAICompatibleAdapter(
            base_url=_env("LLM_BASE_URL", "http://llm:8080"),
            model=_env("LLM_MODEL", "qwen2.5-coder-7b-instruct-abliterated-q4_k_m"),
            temperature=_env_float("LLM_TEMPERATURE", 0.3),
            max_tokens=_env_int("LLM_MAX_TOKENS", 2048),
        )
    raise ValueError(f"llm={kind!r}: допустимо {', '.join(ADAPTER_CATALOG[SLOT_LLM])}")


def _resolve_slot(slot: str, adapter_map: Mapping[str, str] | None) -> str:
    """Провайдер слота: карта топологии > env > дефолт (каталог валидируется)."""
    catalog = ADAPTER_CATALOG[slot]
    default = _DEFAULT_ADAPTERS[slot]
    if adapter_map is not None and slot in adapter_map:
        provider = str(adapter_map[slot]).strip().lower()
    else:
        provider = _env(_ENV_KEYS[slot], default).strip().lower()
    if slot == SLOT_RERANKER and provider in ("none", "disabled", ""):
        provider = "noop"
    if not provider:
        provider = default
    if provider not in catalog:
        raise ValueError(f"{slot}={provider!r}: допустимо {', '.join(catalog)}")
    return provider


def build_adapters(adapter_map: Mapping[str, str] | None = None) -> Adapters:
    """Сборка всех провайдеров по карте топологии/env (M3, add-topology-adapters).

    `adapter_map` — карта слотов из Topology (операторский выбор); отсутствующие слоты
    резолвятся из env/дефолтов. `None` — поведение M2 (только env/дефолт).
    """
    kinds = {slot: _resolve_slot(slot, adapter_map) for slot in ADAPTER_CATALOG}
    return Adapters(
        graph_store=_build_graph(kinds[SLOT_GRAPH_STORE]),
        vector_store=_build_vector(kinds[SLOT_VECTOR_STORE]),
        embedder=_build_embedder(kinds[SLOT_EMBEDDINGS]),
        reranker=_build_reranker(kinds[SLOT_RERANKER]),
        llm=_build_llm(kinds[SLOT_LLM]),
    )


def build_graph_store() -> GraphStoreProvider:
    return _build_graph(_resolve_slot(SLOT_GRAPH_STORE, None))


def build_vector_store() -> VectorStoreProvider:
    return _build_vector(_resolve_slot(SLOT_VECTOR_STORE, None))


def build_embedder() -> Embedder:
    return _build_embedder(_resolve_slot(SLOT_EMBEDDINGS, None))


def build_reranker() -> Reranker:
    return _build_reranker(_resolve_slot(SLOT_RERANKER, None))


def build_llm() -> LLMInference:
    return _build_llm(_resolve_slot(SLOT_LLM, None))