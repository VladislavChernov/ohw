"""Фабрика адаптеров retrieval-контура по env (M2; runtime-переключение через
Topology Orchestrator / Config — M3). Выбор транспорта — конфигурация, не код ядра (L1-02).

Переменные:
- GRAPH_STORE / VECTOR_STORE = neo4j | inmemory
- NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD
- EMBEDDER = deterministic (M2)
- RERANKER = noop (M2)
- LLM_ADAPTER = openai | fake; LLM_BASE_URL / LLM_MODEL / LLM_TEMPERATURE / LLM_MAX_TOKENS
"""

from __future__ import annotations

import os

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


def build_graph_store() -> GraphStoreProvider:
    kind = _env("GRAPH_STORE", "inmemory").strip().lower()
    if kind == "inmemory":
        return InMemoryGraphStore()
    if kind == "neo4j":
        return Neo4jGraphStore(
            uri=_env("NEO4J_URI", "bolt://neo4j:7687"),
            user=_env("NEO4J_USER", "neo4j"),
            password=_env("NEO4J_PASSWORD", "graphrag"),
        )
    raise ValueError(f"GRAPH_STORE={kind!r}: допустимо neo4j|inmemory")


def build_vector_store() -> VectorStoreProvider:
    kind = _env("VECTOR_STORE", "inmemory").strip().lower()
    if kind == "inmemory":
        return InMemoryVectorStore()
    if kind == "neo4j":
        return Neo4jVectorStore(
            uri=_env("NEO4J_URI", "bolt://neo4j:7687"),
            user=_env("NEO4J_USER", "neo4j"),
            password=_env("NEO4J_PASSWORD", "graphrag"),
        )
    raise ValueError(f"VECTOR_STORE={kind!r}: допустимо neo4j|inmemory")


def build_embedder() -> Embedder:
    kind = _env("EMBEDDER", "deterministic").strip().lower()
    if kind == "deterministic":
        return DeterministicEmbedder()
    raise ValueError(f"EMBEDDER={kind!r}: в M2 доступен только deterministic")


def build_reranker() -> Reranker:
    kind = _env("RERANKER", "noop").strip().lower()
    if kind in ("noop", "none", "disabled", ""):
        return NoOpRerankerAdapter()
    raise ValueError(f"RERANKER={kind!r}: в M2 доступен только noop")


def build_llm() -> LLMInference:
    kind = _env("LLM_ADAPTER", "openai").strip().lower()
    if kind == "fake":
        return FakeLLM()
    if kind == "openai":
        return OpenAICompatibleAdapter(
            base_url=_env("LLM_BASE_URL", "http://llm:8080"),
            model=_env("LLM_MODEL", "qwen2.5-coder-7b-instruct-abliterated-q4_k_m"),
            temperature=_env_float("LLM_TEMPERATURE", 0.3),
            max_tokens=_env_int("LLM_MAX_TOKENS", 2048),
        )
    raise ValueError(f"LLM_ADAPTER={kind!r}: допустимо openai|fake")