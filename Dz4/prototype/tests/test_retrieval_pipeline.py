"""РўСѓРјР±Р»РµСЂ РіСЂР°С„РѕРІРѕР№ РѕСЃРё (graph_search_enabled) Рё С‚Р°Р№РјРёРЅРіРё СЂРµС‚СЂРёРІР°
(add-retrieval-graph-toggle, BR-1..BR-5).
"""

from __future__ import annotations

from typing import Any

import pytest

from graphrag_proto.retrieval.adapters.base import GraphStoreProvider, Reranker
from graphrag_proto.retrieval.adapters.deterministic import DeterministicEmbedder
from graphrag_proto.retrieval.adapters.inmemory import InMemoryVectorStore
from graphrag_proto.retrieval.adapters.llm import FakeLLM
from graphrag_proto.retrieval.adapters.reranker import NoOpRerankerAdapter
from graphrag_proto.retrieval.pipeline import QueryPipeline, graph_search_enabled
from graphrag_proto.retrieval.profile import DomainProfileLoader, ProfileError
from graphrag_proto.retrieval.semantic_cache import InMemorySemanticCache

PROFILE: dict[str, Any] = {
    "profile": {"name": "it"},
    "retrieval": {
        "graph_search_enabled": True,
        "expansion_direction": "parent",
        "max_depth": 2,
        "max_fanout": 4,
        "max_graph_nodes": 8,
        "graph_boost": 0.2,
    },
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
    def __init__(
        self,
        expansion_rows: list[dict[str, Any]] | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.expansion_calls: list[tuple[list[str], dict[str, Any]]] = []
        self.events = events if events is not None else []
        self._expansion_rows = expansion_rows if expansion_rows is not None else [
            {
                "node_id": "tag:it:parent",
                "canonical_name": "Parent",
                "path": ["tag:it:first", "tag:it:parent"],
                "depth": 1,
                "kind": "parent",
                "origin": "user",
                "confidence": 0.9,
                "source_ids": ["src://graph"],
                "chunk_ids": ["chk:graph"],
                "domain": "it",
            }
        ]

    def expand(
        self,
        context_ids: list[str],
        *,
        direction: str = "parent",
        max_depth: int = 2,
        max_fanout: int = 8,
        max_nodes: int = 32,
    ) -> list[dict[str, Any]]:
        self.events.append("expand")
        self.expansion_calls.append(
            (
                list(context_ids),
                {
                    "direction": direction,
                    "max_depth": max_depth,
                    "max_fanout": max_fanout,
                    "max_nodes": max_nodes,
                },
            )
        )
        return [dict(row) for row in self._expansion_rows]

    def query(
        self,
        cypher: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        raise AssertionError("graph query must not run")

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


class _FixedReranker(Reranker):
    def __init__(self, scores: list[float]) -> None:
        self._scores = scores

    def rerank(self, query: str, chunks: list[dict[str, Any]]) -> list[float]:
        return list(self._scores)


class _RecordingVectorStore(InMemoryVectorStore):
    def __init__(
        self,
        rows: list[dict[str, Any]],
        events: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.rows = rows
        self.events = events if events is not None else []
        self.calls: list[tuple[list[float], int, str | None]] = []

    def vector_search(
        self,
        embedding: list[float],
        top_k: int = 5,
        domain: str | None = None,
    ) -> list[dict[str, Any]]:
        self.events.append("vector")
        self.calls.append((embedding, top_k, domain))
        return [dict(row) for row in self.rows[:top_k]]


def _vector_rows() -> list[dict[str, Any]]:
    return [
        {
            "chunk_id": "chk:first",
            "score": 0.9,
            "text": "first",
            "source_url": "src://first",
            "domain": "it",
            "context_ids": ["tag:it:first"],
            "custom": {"language": "en"},
        },
        {
            "chunk_id": "chk:second",
            "score": 0.8,
            "text": "second",
            "source_url": "src://second",
            "domain": "it",
            "tag_ids": ["tag:it:second"],
        },
    ]


def _pipeline(
    profile: dict[str, Any],
    graph: GraphStoreProvider,
    vector_store: InMemoryVectorStore | None = None,
) -> QueryPipeline:
    return QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=graph,
        vector_store=vector_store or InMemoryVectorStore(),
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="РѕС‚РІРµС‚"),
        profile_loader=_StubLoader(profile),
    )


def test_graph_search_experiment_disabled_by_default() -> None:
    assert not graph_search_enabled({"retrieval": {}})
    assert not graph_search_enabled({})
    assert graph_search_enabled({"retrieval": {"graph_search_enabled": True}})
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


class _BrokenProfileLoader(DomainProfileLoader):
    def load(self, domain: str | None = None) -> dict[str, Any]:
        raise ProfileError("profile unavailable")


def test_strict_pipeline_fails_on_profile_error() -> None:
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=_CountingGraphStore(),
        vector_store=InMemoryVectorStore(),
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="ответ"),
        profile_loader=_BrokenProfileLoader(),
        strict_profile=True,
    )
    with pytest.raises(ProfileError):
        pipe.run("база данных", domain="it", generate=False)


def test_strict_pipeline_accepts_profile_without_ontology() -> None:
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=_CountingGraphStore(),
        vector_store=_RecordingVectorStore(_vector_rows()),
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="ответ"),
        profile_loader=_StubLoader(
            {
                "profile": {"name": "it"},
                "retrieval": {"graph_search_enabled": False},
            }
        ),
        strict_profile=True,
    )

    done = pipe.run("seed", domain="it", generate=False)

    assert done["text"] == ""
    assert done["graph_degraded"] is False


def test_strict_pipeline_accepts_bounded_expansion_config() -> None:
    graph = _CountingGraphStore()
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=graph,
        vector_store=_RecordingVectorStore(_vector_rows()),
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="ответ"),
        profile_loader=_StubLoader(
            {
                "profile": {"name": "it"},
                "retrieval": {
                    "graph_search_enabled": True,
                    "expansion_direction": "related",
                    "max_depth": 1,
                    "max_fanout": 2,
                    "max_graph_nodes": 3,
                },
            }
        ),
        strict_profile=True,
    )

    done = pipe.run("seed", domain="it", generate=False)

    assert graph.expansion_calls == [
        (
            ["tag:it:first", "tag:it:second"],
            {"direction": "related", "max_depth": 1, "max_fanout": 2, "max_nodes": 3},
        )
    ]
    assert done["graph_degraded"] is False


def test_empty_graph_projection_is_degraded_and_not_cached() -> None:
    cache = InMemorySemanticCache(threshold=0.80, ttl_s=0)
    graph = _CountingGraphStore(expansion_rows=[])
    vector = _RecordingVectorStore(_vector_rows())
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=graph,
        vector_store=vector,
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="ответ"),
        profile_loader=_StubLoader(PROFILE),
        semantic_cache=cache,
    )

    first = pipe.run("seed", trace=True)
    assert first["graph_degraded"] is True
    assert first["trace"][1]["reason"] == "empty_projection"
    assert cache.stats()["entries"] == 0

    pipe.run("seed")
    assert cache.stats()["entries"] == 0


def test_graph_boost_is_applied_after_external_reranker() -> None:
    graph = _CountingGraphStore(
        expansion_rows=[
            {
                "node_id": "tag:it:first",
                "canonical_name": "First",
                "path": ["tag:it:first"],
                "depth": 0,
                "kind": "parent",
                "source_ids": ["src://first"],
                "domain": "it",
            }
        ]
    )
    vector = _RecordingVectorStore(_vector_rows())
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=graph,
        vector_store=vector,
        reranker=_FixedReranker([0.1, 0.9]),
        llm=FakeLLM(text="ответ"),
        profile_loader=_StubLoader(PROFILE),
    )

    done = pipe.run("seed", generate=False, trace=True)

    rerank = next(event for event in done["trace"] if event.get("stage") == "rerank")
    scores = {item["chunk_id"]: item["after"] for item in rerank["scores"]}
    assert scores["chk:first"] == pytest.approx(0.3)
    assert scores["chk:second"] == pytest.approx(0.9)


def test_pipeline_disabled_preserves_vector_baseline() -> None:
    profile: dict[str, Any] = {
        **PROFILE,
        "retrieval": {**PROFILE["retrieval"], "graph_search_enabled": False},
    }
    graph = _CountingGraphStore()
    vector = _RecordingVectorStore(_vector_rows())
    events: list[tuple[str, dict[str, Any]]] = []

    done = _pipeline(profile, graph, vector).run(
        "seed",
        emit=lambda t, p: events.append((t, p)),
        generate=False,
        trace=True,
    )

    assert graph.expansion_calls == []
    assert vector.calls[0][1:] == (5, "it")
    assert ("status", {"stage": "graph", "enabled": True}) not in events
    graph_trace = next(event for event in done["trace"] if event.get("stage") == "graph")
    assert graph_trace["enabled"] is False
    assert graph_trace["skeleton_rows"] == []
    vector_trace = next(event for event in done["trace"] if event.get("stage") == "vector")
    assert [candidate["chunk_id"] for candidate in vector_trace["candidates"]] == [
        "chk:first",
        "chk:second",
    ]
    assert [
        source["source_url"]
        for source in done["sources"]
        if source["axis"] == "vector"
    ] == ["src://first", "src://second"]
    assert done["graph_degraded"] is False
    assert done["retrieval_time_s"] >= 0.0
    assert done["total_time_s"] >= done["retrieval_time_s"]


def test_pipeline_vector_results_seed_bounded_expansion() -> None:
    order: list[str] = []
    graph = _CountingGraphStore(events=order)
    vector = _RecordingVectorStore(_vector_rows(), events=order)
    events: list[tuple[str, dict[str, Any]]] = []

    done = _pipeline(PROFILE, graph, vector).run(
        "seed",
        emit=lambda t, p: events.append((t, p)),
        generate=False,
        trace=True,
    )

    assert order == ["vector", "expand"]
    assert graph.expansion_calls == [
        (
            ["tag:it:first", "tag:it:second"],
            {"direction": "parent", "max_depth": 2, "max_fanout": 4, "max_nodes": 8},
        )
    ]
    assert ("status", {"stage": "graph", "enabled": True}) in events
    expansion_trace = next(
        event for event in done["trace"] if event.get("stage") == "graph_expansion"
    )
    assert expansion_trace["seed_chunk_ids"] == ["chk:first", "chk:second"]
    assert expansion_trace["context_ids"] == ["tag:it:first", "tag:it:second"]
    assert expansion_trace["paths"] == [["tag:it:first", "tag:it:parent"]]
    assert expansion_trace["depths"] == [1]
    vector_trace = next(event for event in done["trace"] if event.get("stage") == "vector")
    assert [candidate["chunk_id"] for candidate in vector_trace["candidates"]] == [
        "chk:first",
        "chk:second",
    ]
    assert {
        "source_url": "src://graph",
        "relevance": 1.0,
        "axis": "graph",
    } in done["sources"]
    assert done["graph_degraded"] is False


def test_pipeline_env_disabled_overrides_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = _CountingGraphStore()
    vector = _RecordingVectorStore(_vector_rows())
    monkeypatch.setenv("RETRIEVAL_GRAPH_ENABLED", "false")

    _pipeline(PROFILE, graph, vector).run("seed", generate=False)

    assert graph.expansion_calls == []
    assert graph.events == []


def test_pipeline_graph_failure_falls_back_to_vector_baseline() -> None:
    class FailingGraphStore(_CountingGraphStore):
        def expand(
            self,
            context_ids: list[str],
            *,
            direction: str = "parent",
            max_depth: int = 2,
            max_fanout: int = 8,
            max_nodes: int = 32,
        ) -> list[dict[str, Any]]:
            super().expand(
                context_ids,
                direction=direction,
                max_depth=max_depth,
                max_fanout=max_fanout,
                max_nodes=max_nodes,
            )
            raise RuntimeError("graph unavailable")

    graph = FailingGraphStore()
    vector = _RecordingVectorStore(_vector_rows())

    done = _pipeline(PROFILE, graph, vector).run("seed", generate=False, trace=True)

    assert done["graph_degraded"] is True
    assert [
        source["source_url"]
        for source in done["sources"]
        if source["axis"] == "vector"
    ] == ["src://first", "src://second"]
    degraded_trace = next(
        event for event in done["trace"] if event.get("stage") == "graph_expansion"
    )
    assert degraded_trace["degraded"] is True


def test_pipeline_shutdown_rejects_new_runs() -> None:
    pipe = _pipeline(PROFILE, _CountingGraphStore())
    pipe.shutdown()
    with pytest.raises(RuntimeError):
        pipe.run("как устроена база данных")


# --- Semantic Cache (бандл 3/3) ----------------------------------------


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
    profile = {
        **PROFILE,
        "retrieval": {**PROFILE["retrieval"], "graph_search_enabled": False},
    }
    return QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=_CountingGraphStore(),
        vector_store=InMemoryVectorStore(),
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="ответ"),
        profile_loader=_StubLoader(profile),
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


# --- Ревизия данных в query-контуре (ADR-026) ------------------------

def test_done_carries_revision_on_generate() -> None:
    cache = InMemorySemanticCache(threshold=0.80, ttl_s=0)
    pipe = _pipeline_with_cache(cache)
    done = pipe.run("как устроена база данных", revision="revA")
    assert done["revision"] == "revA"
    assert done["cache_hit"] is False


def test_done_carries_revision_on_hit() -> None:
    cache = InMemorySemanticCache(threshold=0.80, ttl_s=0)
    pipe = _pipeline_with_cache(cache)
    pipe.run("как устроена база данных", revision="revA")
    done_hit = pipe.run("как устроена база данных", revision="revA")
    assert done_hit["cache_hit"] is True
    assert done_hit["revision"] == "revA"


def test_done_revision_defaults_none() -> None:
    pipe = _pipeline_with_cache()
    done = pipe.run("как устроена база данных")
    assert done["revision"] is None


def test_epoch_bump_hit_only_same_revision() -> None:
    cache = InMemorySemanticCache(threshold=0.80, ttl_s=0)
    pipe = _pipeline_with_cache(cache)
    pipe.run("как устроена база данных", revision="revA")
    # bump: та же формулировка под новой ревизией -> miss -> полный цикл
    done_bump = pipe.run("как устроена база данных", revision="revB")
    assert done_bump["cache_hit"] is False
    assert done_bump["revision"] == "revB"
