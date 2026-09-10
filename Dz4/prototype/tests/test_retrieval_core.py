"""Ретривер, Context Assembly и QueryPipeline (L1-04, L3-03/L3-04, 8.3)."""

from __future__ import annotations

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
from graphrag_proto.retrieval.retrievers import VectorRetriever, build_cypher, extract_query_terms

PROFILE = {
    "ontology": {"node_types": [{"type": "Requirement"}, {"type": "Concept"}, {"type": "Contract"}]},
    "retrieval": {
        "cypher_template": (
            "MATCH (n)\n"
            "WHERE any(t IN $terms WHERE toLower(n.canonical_name) CONTAINS toLower(t) "
            "OR toLower(n.name) CONTAINS toLower(t))\n"
            "OPTIONAL MATCH (n)-[r]-(m)\n"
            "WHERE m:{node_labels}\n"
            "RETURN n, m, type(r) AS rel_type\n"
            "LIMIT $max_nodes"
        ),
    },
    "context_assembly": {"max_tokens": 4096},
}


def test_build_cypher_labels_and_template() -> None:
    cypher, labels = build_cypher(PROFILE)
    assert labels == ["Requirement", "Concept", "Contract"]
    assert "m:Requirement" in cypher
    assert "m:Concept" in cypher
    assert "{node_labels}" not in cypher


def test_extract_query_terms_normalizes() -> None:
    terms = extract_query_terms("  База данных  ")
    assert terms[0] == "база данных"
    assert "база" in terms and "данных" in terms


def test_vector_retriever_returns_top_k() -> None:
    store = InMemoryVectorStore()
    q = deterministic_embedding("запрос по базе")
    store.upsert_vectors(
        [
            {
                "chunk_id": "chk:1",
                "embedding": q,
                "metadata": {"text": "запрос по базе", "source_url": "s://1"},
            },
            {
                "chunk_id": "chk:2",
                "embedding": deterministic_embedding("другой"),
                "metadata": {"text": "другой", "source_url": "s://2"},
            },
        ]
    )
    hits = VectorRetriever(store, top_k=2).retrieve(q)
    assert [h["chunk_id"] for h in hits] == ["chk:1", "chk:2"]


def test_context_assembly_skeleton_first_and_eviction() -> None:
    skeleton = [{"n": {"canonical_name": "База данных"}, "m": {"canonical_name": "SQL"}, "rel_type": "SUPERSET_OF"}]
    huge_text = " ".join(["слово"] * 3000)  # много токенов
    chunks = [
        {"text": huge_text, "source_url": "s://huge", "score": 0.1},
        {"text": "короткая выжившая", "source_url": "s://keep", "score": 0.9},
    ]
    result = ContextAssembly(max_tokens=64).assemble(skeleton, chunks)
    assert result.skeleton_count == 1
    assert "База данных ~[SUPERSET_OF]~ SQL" in result.text
    assert "короткая выжившая" in result.text
    assert huge_text not in result.text
    assert result.body_count == 1
    assert result.dropped == 1


def test_context_assembly_evicts_lowest_score_first() -> None:
    skeleton = [{"n": {"canonical_name": "X"}}]
    chunks = [
        {"text": "низкий", "source_url": "s://low", "score": 0.1},
        {"text": "средний", "source_url": "s://mid", "score": 0.5},
        {"text": "высокий", "source_url": "s://high", "score": 0.9},
    ]
    result = ContextAssembly(max_tokens=7).assemble(skeleton, chunks)  # скелет(1) + 2 строки тела
    assert "высокий" in result.text
    assert "средний" in result.text
    assert "низкий" not in result.text
    assert result.body_count == 2
    assert result.dropped == 1


def test_build_sources_unique_by_source_url() -> None:
    chunks = [
        {"source_url": "s://a", "score": 0.5},
        {"source_url": "s://a", "score": 0.9},
        {"source_url": "s://b", "score": 0.4},
    ]
    sources = build_sources(chunks)
    assert sources == [{"source_url": "s://a", "relevance": 0.9}, {"source_url": "s://b", "relevance": 0.4}]


def test_query_pipeline_emits_events_and_done(tmp_path) -> None:
    graph = InMemoryGraphStore()
    graph.upsert_nodes(
        [
            {
                "node_id": "ent:база",
                "labels": ["Concept"],
                "properties": {"canonical_name": "База данных", "source_ids": ["s://1"]},
            }
        ]
    )
    vector = InMemoryVectorStore()
    query_text = "Как устроена база данных?"
    query_embedding = deterministic_embedding(query_text)
    vector.upsert_vectors(
        [
            {
                "chunk_id": "chk:1",
                "embedding": query_embedding,
                "metadata": {"text": "База данных — набор данных.", "source_url": "s://1"},
            }
        ]
    )

    events: list[tuple[str, dict]] = []
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=graph,
        vector_store=vector,
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="Ответ: база данных это набор данных."),
        profile_loader=DomainProfileLoader(),
    )
    pipe.run(query_text, domain="it", emit=lambda t, p: events.append((t, p)))

    types = [t for t, _ in events]
    for stage in ("embedding", "vector", "rerank", "llm"):
        assert ("status", {"stage": stage}) in events
    assert ("status", {"stage": "graph", "enabled": True}) in events
    assert "done" in types
    assert any(t == "token" for t in types)

    done = next(p for t, p in events if t == "done")
    assert done["sources"]  # source_url-источники
    assert done["text"] == "Ответ: база данных это набор данных."
    assert isinstance(done["generation_time_s"], float)
    assert isinstance(done["retrieval_time_s"], float)
    assert isinstance(done["total_time_s"], float)
    assert done["retrieval_time_s"] <= done["total_time_s"]


def test_query_pipeline_without_graph_match_still_answers(tmp_path) -> None:
    """Пустая графовая ось не должна валить пайплайн (resilience)."""
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=InMemoryGraphStore(),
        vector_store=InMemoryVectorStore(),
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="ответ"),
        profile_loader=DomainProfileLoader(),
    )
    result = pipe.run("вопрос без данных", domain="it")
    assert result["text"] == "ответ"
    assert result["sources"] == []