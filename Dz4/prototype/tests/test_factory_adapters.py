"""Factory: каталог, build_adapters (карта топологии > env > дефолт), M2-compat."""

from __future__ import annotations

import pytest

from graphrag_proto.retrieval.adapters.factory import (
    ADAPTER_CATALOG,
    Adapters,
    build_adapters,
    build_graph_store,
    build_llm,
    build_reranker,
    build_vector_store,
)
from graphrag_proto.retrieval.adapters.inmemory import InMemoryGraphStore, InMemoryVectorStore
from graphrag_proto.retrieval.adapters.llm import FakeLLM, OpenAICompatibleAdapter
from graphrag_proto.retrieval.adapters.neo4j import Neo4jGraphStore, Neo4jVectorStore
from graphrag_proto.retrieval.adapters.reranker import NoOpRerankerAdapter
from graphrag_proto.topology_service.catalog import SLOT_ORDER, available_providers


def test_catalog_defaults_cover_all_slots() -> None:
    assert set(ADAPTER_CATALOG) == set(SLOT_ORDER)
    assert SLOT_ORDER == ("graph_store", "vector_store", "embeddings", "reranker", "llm")


def test_available_matches_catalog() -> None:
    assert available_providers() == {slot: list(providers) for slot, providers in ADAPTER_CATALOG.items()}


def test_build_adapters_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRAPH_STORE", raising=False)
    monkeypatch.delenv("VECTOR_STORE", raising=False)
    monkeypatch.delenv("EMBEDDER", raising=False)
    monkeypatch.delenv("RERANKER", raising=False)
    monkeypatch.delenv("LLM_ADAPTER", raising=False)
    adapters = build_adapters()
    assert isinstance(adapters, Adapters)
    assert isinstance(adapters.graph_store, InMemoryGraphStore)
    assert isinstance(adapters.vector_store, InMemoryVectorStore)
    assert isinstance(adapters.reranker, NoOpRerankerAdapter)
    assert isinstance(adapters.llm, OpenAICompatibleAdapter)


def test_adapter_map_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPH_STORE", "neo4j")
    monkeypatch.setenv("VECTOR_STORE", "neo4j")
    adapters = build_adapters(adapter_map={"graph_store": "inmemory", "vector_store": "inmemory"})
    assert isinstance(adapters.graph_store, InMemoryGraphStore)
    assert isinstance(adapters.vector_store, InMemoryVectorStore)


def test_adapter_map_partial_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_ADAPTER", "fake")
    adapters = build_adapters(adapter_map={"vector_store": "inmemory"})
    assert isinstance(adapters.llm, FakeLLM)


def test_build_adapters_unknown_provider_raises() -> None:
    with pytest.raises(ValueError, match="graph_store"):
        build_adapters(adapter_map={"graph_store": "postgres"})


def test_reranker_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    for alias in ("none", "disabled", ""):
        monkeypatch.setenv("RERANKER", alias)
        assert isinstance(build_reranker(), NoOpRerankerAdapter)


@pytest.mark.parametrize(
    ("provider", "expected"),
    [("inmemory", InMemoryGraphStore), ("neo4j", Neo4jGraphStore)],
)
def test_build_graph_store_env(monkeypatch: pytest.MonkeyPatch, provider: str, expected: type) -> None:
    monkeypatch.setenv("GRAPH_STORE", provider)
    assert isinstance(build_graph_store(), expected)


@pytest.mark.parametrize(
    ("provider", "expected"),
    [("inmemory", InMemoryVectorStore), ("neo4j", Neo4jVectorStore)],
)
def test_build_vector_store_env(monkeypatch: pytest.MonkeyPatch, provider: str, expected: type) -> None:
    monkeypatch.setenv("VECTOR_STORE", provider)
    assert isinstance(build_vector_store(), expected)


def test_build_llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_ADAPTER", "fake")
    assert isinstance(build_llm(), FakeLLM)


def test_unknown_env_provider_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VECTOR_STORE", "pinecone")
    with pytest.raises(ValueError, match="vector_store"):
        build_adapters()