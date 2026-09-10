"""РўСѓРјР±Р»РµСЂ РіСЂР°С„РѕРІРѕР№ РѕСЃРё (graph_search_enabled) Рё С‚Р°Р№РјРёРЅРіРё СЂРµС‚СЂРёРІР°
(add-retrieval-graph-toggle, BR-1..BR-5).
"""

from __future__ import annotations

from typing import Any

import pytest

from graphrag_proto.retrieval.adapters.base import GraphStoreProvider
from graphrag_proto.retrieval.adapters.deterministic import DeterministicEmbedder
from graphrag_proto.retrieval.adapters.inmemory import InMemoryVectorStore
from graphrag_proto.retrieval.adapters.llm import FakeLLM
from graphrag_proto.retrieval.adapters.reranker import NoOpRerankerAdapter
from graphrag_proto.retrieval.pipeline import QueryPipeline, graph_search_enabled
from graphrag_proto.retrieval.profile import DomainProfileLoader

PROFILE: dict[str, Any] = {
    "ontology": {"node_types": [{"type": "Concept"}]},
    "retrieval": {"graph_search_enabled": True, "cypher_template": "MATCH (n) RETURN n"},
    "context_assembly": {"max_tokens": 4096},
}


class _StubLoader(DomainProfileLoader):
    def __init__(self, profile: dict[str, Any]) -> None:
        super().__init__()
        self._profile = profile

    def active_domain(self) -> str:
        return "it"

    def load(self, domain: str | None = None) -> dict[str, Any]:
        return self._profile


class _CountingGraphStore(GraphStoreProvider):
    """Р“СЂР°С„РѕРІС‹Р№ РґРІРѕР№РЅРёРє, СЃС‡РёС‚Р°СЋС‰РёР№ РѕР±СЂР°С‰РµРЅРёСЏ Рє query()."""

    def __init__(self) -> None:
        self.queries: list[tuple[str, dict[str, Any] | None]] = []

    def query(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self.queries.append((cypher, params))
        return []

    def upsert_nodes(self, nodes: list[dict[str, Any]]) -> None:
        return None

    def upsert_edges(self, edges: list[dict[str, Any]]) -> None:
        return None

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        return None

    def delete_node(self, node_id: str) -> bool:
        return False

    def list_chunk_ids_of_source(self, source_id: str) -> list[str]:
        return []


def _pipeline(profile: dict[str, Any], graph: GraphStoreProvider) -> QueryPipeline:
    return QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=graph,
        vector_store=InMemoryVectorStore(),
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="РѕС‚РІРµС‚"),
        profile_loader=_StubLoader(profile),
    )


def test_graph_search_enabled_default_true() -> None:
    assert graph_search_enabled({"retrieval": {}})
    assert graph_search_enabled({})
    assert not graph_search_enabled({"retrieval": {"graph_search_enabled": False}})


def test_graph_search_enabled_env_overrides_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    profile_on: dict[str, Any] = {"retrieval": {"graph_search_enabled": True}}
    profile_off: dict[str, Any] = {"retrieval": {"graph_search_enabled": False}}

    monkeypatch.setenv("RETRIEVAL_GRAPH_ENABLED", "false")
    assert not graph_search_enabled(profile_on)

    monkeypatch.setenv("RETRIEVAL_GRAPH_ENABLED", "TRUE")
    assert graph_search_enabled(profile_off)

    monkeypatch.delenv("RETRIEVAL_GRAPH_ENABLED")
    assert graph_search_enabled(profile_on)


def test_pipeline_disabled_skips_graph_store() -> None:
    profile: dict[str, Any] = {
        **PROFILE,
        "retrieval": {**PROFILE["retrieval"], "graph_search_enabled": False},
    }
    graph = _CountingGraphStore()
    events: list[tuple[str, dict[str, Any]]] = []

    done = _pipeline(profile, graph).run("РєР°Рє СѓСЃС‚СЂРѕРµРЅР° Р±Р°Р·Р° РґР°РЅРЅС‹С…", emit=lambda t, p: events.append((t, p)))

    assert graph.queries == []
    assert ("status", {"stage": "graph", "enabled": False}) in events
    assert done["retrieval_time_s"] >= 0.0
    assert done["total_time_s"] >= done["retrieval_time_s"]


def test_pipeline_enabled_calls_graph_store() -> None:
    graph = _CountingGraphStore()
    events: list[tuple[str, dict[str, Any]]] = []

    _pipeline(PROFILE, graph).run("РєР°Рє СѓСЃС‚СЂРѕРµРЅР° Р±Р°Р·Р° РґР°РЅРЅС‹С…", emit=lambda t, p: events.append((t, p)))

    assert len(graph.queries) == 1
    assert ("status", {"stage": "graph", "enabled": True}) in events


def test_pipeline_env_disabled_overrides_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = _CountingGraphStore()
    monkeypatch.setenv("RETRIEVAL_GRAPH_ENABLED", "false")

    _pipeline(PROFILE, graph).run("РєР°Рє СѓСЃС‚СЂРѕРµРЅР° Р±Р°Р·Р° РґР°РЅРЅС‹С…")

    assert graph.queries == []
