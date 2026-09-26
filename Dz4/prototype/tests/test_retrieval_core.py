"""Векторный baseline, helpers и optional lightweight graph expansion."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from graphrag_proto.retrieval.adapters.deterministic import (
    DeterministicEmbedder,
    deterministic_embedding,
)
from graphrag_proto.retrieval.adapters.inmemory import InMemoryGraphStore, InMemoryVectorStore
from graphrag_proto.retrieval.adapters.llm import FakeLLM
from graphrag_proto.retrieval.adapters.reranker import NoOpRerankerAdapter
from graphrag_proto.retrieval.context import ContextAssembly
from graphrag_proto.retrieval.pipeline import QueryPipeline, build_sources
from graphrag_proto.retrieval.profile import DomainProfileLoader
from graphrag_proto.retrieval.retrievers import GraphRetriever, VectorRetriever, extract_query_terms

VECTOR_PROFILE: dict[str, Any] = {
    "profile": {"name": "it"},
    "retrieval": {"graph_search_enabled": False},
    "context_assembly": {"max_tokens": 4096},
}

GRAPH_PROFILE: dict[str, Any] = {
    "profile": {"name": "it"},
    "retrieval": {
        "graph_search_enabled": True,
        "expansion_direction": "parent",
        "max_depth": 1,
        "max_fanout": 2,
        "max_graph_nodes": 3,
        "graph_boost": 0.0,
    },
    "context_assembly": {"max_tokens": 4096},
}


class _ProfileLoader(DomainProfileLoader):
    def __init__(self, profile: dict[str, Any]) -> None:
        self._profile = profile

    def load(self, domain: str | None = None) -> dict[str, Any]:
        return self._profile


class _ExpansionGraph(InMemoryGraphStore):
    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        error: Exception | None = None,
    ) -> None:
        super().__init__()
        self.rows = rows or []
        self.error = error
        self.expansion_calls: list[tuple[list[str], dict[str, Any]]] = []
        self.query_called = False

    def expand(
        self,
        context_ids: list[str],
        *,
        direction: str = "both",
        kinds: Sequence[str] | None = None,
        max_depth: int = 2,
        max_fanout: int = 8,
        max_nodes: int = 32,
    ) -> list[dict[str, Any]]:
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
        if self.error is not None:
            raise self.error
        return [dict(row) for row in self.rows]

    def query(
        self,
        cypher: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        self.query_called = True
        return super().query(cypher, params)


def _pipeline(
    profile: dict[str, Any],
    graph: InMemoryGraphStore,
    vector: InMemoryVectorStore,
    answer: str = "ответ",
) -> QueryPipeline:
    return QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=graph,
        vector_store=vector,
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text=answer),
        profile_loader=_ProfileLoader(profile),
    )


def test_extract_query_terms_normalizes() -> None:
    terms = extract_query_terms("  База данных  ")
    assert terms[0] == "база данных"
    assert "база" in terms and "данных" in terms


def test_vector_retriever_returns_top_k_with_generic_context_metadata() -> None:
    store = InMemoryVectorStore()
    query = deterministic_embedding("запрос по базе")
    store.upsert_vectors(
        [
            {
                "chunk_id": "chk:1",
                "embedding": query,
                "metadata": {
                    "text": "запрос по базе",
                    "source_url": "src://1",
                    "context_ids": ["tag:it:database"],
                },
            },
            {
                "chunk_id": "chk:2",
                "embedding": deterministic_embedding("другой"),
                "metadata": {
                    "text": "другой",
                    "source_url": "src://2",
                    "tag_ids": ["tag:it:other"],
                },
            },
        ]
    )

    hits = VectorRetriever(store, top_k=2).retrieve(query)

    assert [hit["chunk_id"] for hit in hits] == ["chk:1", "chk:2"]
    assert hits[0]["context_ids"] == ["tag:it:database"]
    assert hits[1]["tag_ids"] == ["tag:it:other"]


def test_graph_retriever_expands_generic_nodes_and_filters_empty_provenance() -> None:
    graph = _ExpansionGraph(
        [
            {
                "node_id": "tag:it:district",
                "tag_id": "tag:it:district",
                "canonical_name": "Район",
                "path": ["tag:it:database", "tag:it:district"],
                "depth": 1,
                "kind": "parent",
                "source_ids": ["src://graph"],
                "chunk_ids": ["chk:graph"],
            },
            {
                "node_id": "tag:it:orphan-source",
                "canonical_name": "Без источника",
                "source_ids": [],
                "chunk_ids": ["chk:orphan"],
            },
            {
                "node_id": "tag:it:orphan-chunk",
                "canonical_name": "Без чанка",
                "source_ids": ["src://orphan"],
                "chunk_ids": [],
            },
        ]
    )
    retriever = GraphRetriever(graph, VECTOR_PROFILE)

    rows = retriever.expand(
        ["tag:it:database"],
        direction="parent",
        max_depth=1,
        max_fanout=2,
        max_nodes=3,
    )

    assert graph.expansion_calls == [
        (
            ["tag:it:database"],
            {"direction": "parent", "max_depth": 1, "max_fanout": 2, "max_nodes": 3},
        )
    ]
    assert [row["node_id"] for row in rows] == ["tag:it:district"]
    assert all("_labels" not in row for row in rows)


def test_graph_retriever_skips_empty_seed_and_unsupported_expansion() -> None:
    graph = _ExpansionGraph(error=NotImplementedError("expansion unavailable"))
    retriever = GraphRetriever(graph, VECTOR_PROFILE)

    assert retriever.expand([], max_nodes=3) == []
    assert graph.expansion_calls == []
    with pytest.raises(NotImplementedError):
        retriever.expand(["tag:it:missing"], max_nodes=3)
    assert len(graph.expansion_calls) == 1


def test_context_assembly_skeleton_first_and_eviction() -> None:
    skeleton = [
        {
            "n": {"tag_id": "tag:it:database", "canonical_name": "База данных"},
            "m": {"tag_id": "tag:it:sql", "canonical_name": "SQL"},
            "rel_type": "related",
        }
    ]
    huge_text = " ".join(["слово"] * 3000)
    chunks = [
        {"text": huge_text, "source_url": "src://huge", "score": 0.1},
        {"text": "короткая выжившая", "source_url": "src://keep", "score": 0.9},
    ]

    result = ContextAssembly(max_tokens=64).assemble(skeleton, chunks)

    assert result.skeleton_count == 1
    assert "База данных ~[related]~ SQL" in result.text
    assert "короткая выжившая" in result.text
    assert huge_text not in result.text
    assert result.body_count == 1
    assert result.dropped == 1


def test_context_assembly_evicts_lowest_score_first() -> None:
    skeleton = [{"n": {"canonical_name": "X"}}]
    chunks = [
        {"text": "низкий", "source_url": "src://low", "score": 0.1},
        {"text": "средний", "source_url": "src://mid", "score": 0.5},
        {"text": "высокий", "source_url": "src://high", "score": 0.9},
    ]

    result = ContextAssembly(max_tokens=7).assemble(skeleton, chunks)

    assert "высокий" in result.text
    assert "средний" in result.text
    assert "низкий" not in result.text
    assert result.body_count == 2
    assert result.dropped == 1


def test_context_assembly_enforces_hard_limit_with_large_skeleton() -> None:
    skeleton = [
        {"n": {"canonical_name": "one two three four five six"}},
        {"n": {"canonical_name": "seven eight nine ten"}},
    ]
    chunks = [{"text": "body", "source_url": "src://body", "score": 1.0}]

    result = ContextAssembly(max_tokens=4).assemble(skeleton, chunks)

    assert result.tokens <= 4
    assert result.skeleton_count == 1
    assert result.body_count == 0
    assert result.dropped >= 2


def test_build_sources_unique_by_source_url() -> None:
    chunks = [
        {"source_url": "src://a", "score": 0.5},
        {"source_url": "src://a", "score": 0.9},
        {"source_url": "src://b", "score": 0.4},
    ]

    sources = build_sources(chunks)

    assert sources == [
        {"source_url": "src://a", "relevance": 0.9, "axis": "vector"},
        {"source_url": "src://b", "relevance": 0.4, "axis": "vector"},
    ]


def test_build_sources_keeps_axes_and_deduplicates_by_pair() -> None:
    chunks = [
        {"source_url": "src://shared", "score": 0.8},
        {"source_url": "src://shared", "score": 0.95},
        {"source_url": "src://vector-only", "score": 0.6},
    ]
    skeleton = [
        {
            "n": {
                "tag_id": "tag:it:indexing",
                "canonical_name": "Индексация",
                "source_ids": ["src://graph-only", "src://shared"],
            },
            "m": None,
            "rel_type": "related",
        },
        {
            "n": {
                "tag_id": "tag:it:oauth",
                "name": "OAuth",
                "source_ids": ["src://graph-only"],
            },
            "m": None,
            "rel_type": None,
        },
    ]

    sources = build_sources(chunks, skeleton)
    by_pair = {(source["source_url"], source["axis"]): source for source in sources}

    assert by_pair[("src://shared", "vector")]["relevance"] == 0.95
    assert by_pair[("src://shared", "graph")]["relevance"] == 1.0
    assert by_pair[("src://graph-only", "graph")] == {
        "source_url": "src://graph-only",
        "relevance": 1.0,
        "axis": "graph",
    }
    assert ("src://vector-only", "vector") in by_pair
    assert len(sources) == 4
    assert sources[0]["axis"] == "graph"


def test_query_pipeline_vector_only_baseline_skips_graph() -> None:
    query = "Как устроена база данных?"
    embedding = deterministic_embedding(query)
    vector = InMemoryVectorStore()
    vector.upsert_vectors(
        [
            {
                "chunk_id": "chk:it",
                "embedding": embedding,
                "metadata": {
                    "text": "База данных — набор данных.",
                    "source_url": "src://it",
                    "domain": "it",
                    "context_ids": ["tag:it:database"],
                },
            },
            {
                "chunk_id": "chk:library",
                "embedding": embedding,
                "metadata": {
                    "text": "Библиотечная база данных.",
                    "source_url": "src://library",
                    "domain": "library",
                    "context_ids": ["tag:library:database"],
                },
            },
        ]
    )
    graph = _ExpansionGraph()
    events: list[tuple[str, dict[str, Any]]] = []

    result = _pipeline(
        VECTOR_PROFILE,
        graph,
        vector,
        answer="Ответ: база данных это набор данных.",
    ).run(
        query,
        domain="it",
        emit=lambda event, payload: events.append((event, payload)),
        trace=True,
    )

    assert graph.expansion_calls == []
    assert graph.query_called is False
    assert ("status", {"stage": "graph", "enabled": True}) not in events
    for stage in ("embedding", "vector", "rerank", "llm"):
        assert ("status", {"stage": stage}) in events
    assert any(event == "token" for event, _ in events)
    assert result["text"] == "Ответ: база данных это набор данных."
    assert result["sources"] == [
        {"source_url": "src://it", "relevance": 1.0, "axis": "vector"}
    ]
    assert result["graph_degraded"] is False
    assert isinstance(result["generation_time_s"], float)
    assert isinstance(result["retrieval_time_s"], float)
    assert isinstance(result["total_time_s"], float)
    assert result["retrieval_time_s"] <= result["total_time_s"]
    graph_trace = next(event for event in result["trace"] if event.get("stage") == "graph")
    assert graph_trace["enabled"] is False
    assert graph_trace["skeleton_rows"] == []


def test_query_pipeline_runs_bounded_context_expansion_after_vector_search() -> None:
    query = "База данных"
    embedding = deterministic_embedding(query)
    vector = InMemoryVectorStore()
    vector.upsert_vectors(
        [
            {
                "chunk_id": "chk:it",
                "embedding": embedding,
                "metadata": {
                    "text": "База данных",
                    "source_url": "src://it",
                    "domain": "it",
                    "context_ids": ["tag:it:database"],
                    "tag_ids": ["tag:it:database", "tag:it:storage"],
                },
            },
            {
                "chunk_id": "chk:library",
                "embedding": embedding,
                "metadata": {
                    "text": "Библиотечная база данных",
                    "source_url": "src://library",
                    "domain": "library",
                    "context_ids": ["tag:library:database"],
                },
            },
        ]
    )
    graph = _ExpansionGraph(
        [
            {
                "node_id": "tag:it:storage",
                "tag_id": "tag:it:storage",
                "canonical_name": "Хранилище",
                "path": ["tag:it:database", "tag:it:storage"],
                "depth": 1,
                "kind": "parent",
                "origin": "user",
                "confidence": 0.9,
                "source_ids": ["src://graph"],
                "chunk_ids": ["chk:graph"],
                "domain": "it",
            }
        ]
    )
    events: list[tuple[str, dict[str, Any]]] = []

    result = _pipeline(GRAPH_PROFILE, graph, vector).run(
        query,
        domain="it",
        emit=lambda event, payload: events.append((event, payload)),
        trace=True,
    )

    assert graph.expansion_calls == [
        (
            ["tag:it:database", "tag:it:storage"],
            {"direction": "parent", "max_depth": 1, "max_fanout": 2, "max_nodes": 3},
        )
    ]
    assert graph.query_called is False
    assert ("status", {"stage": "graph", "enabled": True}) in events
    expansion_trace = next(
        event for event in result["trace"] if event.get("stage") == "graph_expansion"
    )
    assert expansion_trace["seed_chunk_ids"] == ["chk:it"]
    assert expansion_trace["context_ids"] == ["tag:it:database", "tag:it:storage"]
    assert expansion_trace["paths"] == [["tag:it:database", "tag:it:storage"]]
    assert expansion_trace["depths"] == [1]
    assert result["sources"] == [
        {"source_url": "src://graph", "relevance": 1.0, "axis": "graph"},
        {"source_url": "src://it", "relevance": 1.0, "axis": "vector"},
    ]
    assert result["graph_degraded"] is False


def test_query_pipeline_graph_failure_preserves_vector_answer() -> None:
    query = "База данных"
    embedding = deterministic_embedding(query)
    vector = InMemoryVectorStore()
    vector.upsert_vectors(
        [
            {
                "chunk_id": "chk:it",
                "embedding": embedding,
                "metadata": {
                    "text": "База данных",
                    "source_url": "src://it",
                    "domain": "it",
                    "context_ids": ["tag:it:database"],
                },
            }
        ]
    )
    graph = _ExpansionGraph(error=RuntimeError("graph unavailable"))

    result = _pipeline(GRAPH_PROFILE, graph, vector, answer="векторный ответ").run(
        query,
        domain="it",
        trace=True,
    )

    assert result["text"] == "векторный ответ"
    assert result["sources"] == [
        {"source_url": "src://it", "relevance": 1.0, "axis": "vector"}
    ]
    assert result["graph_degraded"] is True
    degraded_trace = next(
        event for event in result["trace"] if event.get("stage") == "graph_expansion"
    )
    assert degraded_trace["degraded"] is True


def test_query_pipeline_without_context_nodes_still_answers() -> None:
    result = _pipeline(GRAPH_PROFILE, InMemoryGraphStore(), InMemoryVectorStore()).run(
        "вопрос без данных",
        domain="it",
    )

    assert result["text"] == "ответ"
    assert result["sources"] == []
    assert result["graph_degraded"] is True
