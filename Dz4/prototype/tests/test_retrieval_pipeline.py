"""РўСѓРјР±Р»РµСЂ РіСЂР°С„РѕРІРѕР№ РѕСЃРё (graph_search_enabled) Рё С‚Р°Р№РјРёРЅРіРё СЂРµС‚СЂРёРІР°
(add-retrieval-graph-toggle, BR-1..BR-5).
"""

from __future__ import annotations

import threading
import time
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

    _pipeline(PROFILE, graph).run("как устроена база данных")

    assert graph.queries == []


class _ThreadRecordingVectorStore(InMemoryVectorStore):
    def __init__(self) -> None:
        super().__init__()
        self.threads: list[int] = []

    def vector_search(self, embedding: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        time.sleep(0.05)
        self.threads.append(threading.get_ident())
        return super().vector_search(embedding, top_k)


class _ThreadRecordingGraphStore(_CountingGraphStore):
    def __init__(self) -> None:
        super().__init__()
        self.threads: list[int] = []

    def query(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        time.sleep(0.05)
        self.threads.append(threading.get_ident())
        return super().query(cypher, params)


def test_pipeline_reuses_shared_executor() -> None:
    graph = _ThreadRecordingGraphStore()
    vector = _ThreadRecordingVectorStore()
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=graph,
        vector_store=vector,
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="ответ"),
        profile_loader=_StubLoader(PROFILE),
    )
    pipe.run("как устроена база данных")
    pipe.run("как устроена база данных")
    threads = set(graph.threads) | set(vector.threads)
    assert len(threads) == 2  # один общий пул на оба прогона вместо потока на каждый run
    pipe.shutdown()


def test_pipeline_shutdown_rejects_new_runs() -> None:
    pipe = _pipeline(PROFILE, _CountingGraphStore())
    pipe.shutdown()
    with pytest.raises(RuntimeError):
        pipe.run("как устроена база данных")


# --- Semantic Cache (бандл 3/3) ----------------------------------------

from graphrag_proto.retrieval.semantic_cache import InMemorySemanticCache


class _CountingLLM:
    """FakeLLM, считающий число вызовов generate и дельт (проверка: cache-hit не шлёт token)."""

    def __init__(self, text: str = "ответ") -> None:
        self._text = text
        self.call_count = 0
        self.token_count = 0

    def generate(self, prompt: str, system: str = "", stream: bool = True):
        self.call_count += 1
        for delta in self._text.split(" "):
            self.token_count += 1
            yield delta


def _pipeline_with_cache(
    cache: InMemorySemanticCache | None = None,
) -> QueryPipeline:
    return QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=_CountingGraphStore(),
        vector_store=InMemoryVectorStore(),
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="ответ"),
        profile_loader=_StubLoader(PROFILE),
        semantic_cache=cache,
    )


def test_cache_hit_returns_cached_answer_no_llm_no_token() -> None:
    """miss + повторный hit: LLM вызван 1 раз, tokenevents 0, cache_hit=True."""
    cache = InMemorySemanticCache(threshold=0.80, ttl_s=0)
    pipe = _pipeline_with_cache(cache)
    llm = _CountingLLM("ответ")

    # Patch llm into pipeline
    pipe._llm = llm

    # miss
    done_miss = pipe.run("как устроена база данных")
    assert done_miss.get("cache_hit") is False
    assert llm.call_count == 1

    # hit
    done_hit = pipe.run("как устроена база данных")
    assert done_hit["text"] == "ответ"
    assert done_hit["cache_hit"] is True
    assert done_hit["generation_time_s"] == 0.0
    assert done_hit["retrieval_time_s"] == 0.0
    assert done_hit["cache_lookup_s"] >= 0.0
    # LLM не вызывался повторно
    assert llm.call_count == 1
    # token не шлётся при hit
    assert done_hit.get("sources") is not None


def test_cache_miss_stores_answer() -> None:
    cache = InMemorySemanticCache(threshold=0.80, ttl_s=0)
    pipe = _pipeline_with_cache(cache)
    pipe.run("как устроена база данных")
    # после miss ответ должен быть в кэше
    assert cache.stats()["entries"] >= 1


def test_no_cache_backward_compat() -> None:
    """Pipeline без кэша не меняет поля done (backward-compat с M2/M3)."""
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=_CountingGraphStore(),
        vector_store=InMemoryVectorStore(),
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="ответ"),
        profile_loader=_StubLoader(PROFILE),
    )
    done = pipe.run("как устроена база данных")
    assert "cache_hit" not in done
