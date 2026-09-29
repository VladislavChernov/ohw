"""Контрактные тесты retrieval-адаптеров для vector baseline и lightweight graph."""

from __future__ import annotations

from typing import Self

import pytest

from graphrag_proto.retrieval.adapters.base import VectorStoreProvider
from graphrag_proto.retrieval.adapters.deterministic import (
    DeterministicEmbedder,
    deterministic_embedding,
)
from graphrag_proto.retrieval.adapters.inmemory import InMemoryGraphStore, InMemoryVectorStore
from graphrag_proto.retrieval.adapters.llm import FakeLLM
from graphrag_proto.retrieval.adapters.neo4j import Neo4jGraphStore, Neo4jVectorStore
from graphrag_proto.retrieval.adapters.reranker import NoOpRerankerAdapter
from graphrag_proto.retrieval.adapters.schemas import normalize_vector_row
from graphrag_proto.retrieval.retrievers import VectorRetriever


def _seed(graph: InMemoryGraphStore) -> None:
    graph.upsert_nodes(
        [
            {
                "node_id": "src:it:src://a.txt",
                "labels": ["Source"],
                "properties": {"source_url": "src://a.txt", "domain": "it"},
            },
            {
                "node_id": "chk:abc",
                "labels": ["Chunk"],
                "properties": {
                    "chunk_id": "chk:abc",
                    "text": "База данных — упорядоченный набор данных.",
                    "source_url": "src://a.txt",
                    "domain": "it",
                    "context_ids": ["tag:it:database"],
                },
            },
            {
                "node_id": "tag:it:database",
                "labels": ["ContextNode"],
                "properties": {
                    "type": "ContextNode",
                    "tag_id": "tag:it:database",
                    "canonical_name": "База данных",
                    "aliases": ["Database"],
                    "origin": "user",
                    "source_ids": ["src://a.txt"],
                    "chunk_ids": ["chk:abc"],
                    "domain": "it",
                    "properties": {"language": "ru"},
                },
            },
            {
                "node_id": "tag:it:storage",
                "labels": ["ContextNode"],
                "properties": {
                    "type": "ContextNode",
                    "tag_id": "tag:it:storage",
                    "canonical_name": "Хранилище",
                    "origin": "ai",
                    "source_ids": ["src://a.txt"],
                    "chunk_ids": ["chk:abc"],
                    "domain": "it",
                    "properties": {"language": "ru"},
                },
            },
        ]
    )
    graph.upsert_edges(
        [
            {
                "from_id": "src:it:src://a.txt",
                "to_id": "chk:abc",
                "type": "CONTAINS",
                "properties": {},
            },
            {
                "from_id": "tag:it:database",
                "to_id": "tag:it:storage",
                "type": "RELATED",
                "properties": {"kind": "parent", "origin": "user"},
            },
        ]
    )


def test_deterministic_embedding_unification() -> None:
    text = "База данных"
    assert deterministic_embedding(text) == deterministic_embedding(text)
    assert deterministic_embedding(text) == DeterministicEmbedder().embed(text)
    assert len(deterministic_embedding(text)) == 8
    assert deterministic_embedding(text) != deterministic_embedding("База данных2")


def test_inmemory_context_expansion_uses_tag_id_and_respects_bounds() -> None:
    store = InMemoryGraphStore()
    _seed(store)

    rows = store.expand(
        ["tag:it:database"],
        direction="parent",
        max_depth=2,
        max_fanout=1,
        max_nodes=1,
    )

    assert len(rows) == 1
    assert rows[0] == {
        "node_id": "tag:it:storage",
        "canonical_name": "Хранилище",
        "path": ["tag:it:database", "tag:it:storage"],
        "depth": 1,
        "kind": "parent",
        "source_ids": ["src://a.txt"],
        "chunk_ids": ["chk:abc"],
        "domain": "it",
        "origin": "ai",
        "confidence": None,
        "properties": {"language": "ru"},
    }
    assert "_labels" not in rows[0]


def test_inmemory_expansion_traverses_extraction_kinds_in_both_directions() -> None:
    """Виды из промпта экстракции не должны отсекаться фильтром обхода (ADR-031, open)."""
    store = InMemoryGraphStore()
    store.upsert_nodes(
        [
            {
                "node_id": "tag:it:alpha",
                "labels": ["ContextNode"],
                "properties": {
                    "tag_id": "tag:it:alpha",
                    "canonical_name": "Альфа",
                    "domain": "it",
                    "source_ids": ["src://a.txt"],
                    "chunk_ids": ["chk:abc"],
                },
            },
            {
                "node_id": "tag:it:beta",
                "labels": ["ContextNode"],
                "properties": {
                    "tag_id": "tag:it:beta",
                    "canonical_name": "Бета",
                    "domain": "it",
                    "source_ids": ["src://a.txt"],
                    "chunk_ids": ["chk:abc"],
                },
            },
        ]
    )
    store.upsert_edges(
        [
            {
                "from_id": "tag:it:alpha",
                "to_id": "tag:it:beta",
                "type": "CONTRADICTS",
                "properties": {
                    "source_ids": ["src://a.txt"],
                    "chunk_ids": ["chk:abc"],
                },
            }
        ]
    )

    forward = store.expand(["tag:it:alpha"], max_nodes=3)
    backward = store.expand(["tag:it:beta"], max_nodes=3)

    assert [row["node_id"] for row in forward] == ["tag:it:beta"]
    assert [row["kind"] for row in forward] == ["CONTRADICTS"]
    assert [row["node_id"] for row in backward] == ["tag:it:alpha"]
    assert [row["kind"] for row in backward] == ["CONTRADICTS"]


def test_inmemory_expansion_kinds_narrows_traversal() -> None:
    store = InMemoryGraphStore()
    store.upsert_nodes(
        [
            {
                "node_id": f"tag:it:{name}",
                "labels": ["ContextNode"],
                "properties": {
                    "tag_id": f"tag:it:{name}",
                    "canonical_name": name,
                    "domain": "it",
                    "source_ids": ["src://a.txt"],
                    "chunk_ids": ["chk:abc"],
                },
            }
            for name in ("alpha", "beta", "gamma")
        ]
    )
    store.upsert_edges(
        [
            {
                "from_id": "tag:it:alpha",
                "to_id": "tag:it:beta",
                "type": "CONTRADICTS",
                "properties": {
                    "source_ids": ["src://a.txt"],
                    "chunk_ids": ["chk:abc"],
                },
            },
            {
                "from_id": "tag:it:alpha",
                "to_id": "tag:it:gamma",
                "type": "SIMILAR_TO",
                "properties": {
                    "source_ids": ["src://a.txt"],
                    "chunk_ids": ["chk:abc"],
                },
            },
        ]
    )

    only_similar = store.expand(["tag:it:alpha"], kinds=["SIMILAR_TO"], max_nodes=3)

    assert [row["node_id"] for row in only_similar] == ["tag:it:gamma"]
    assert store.expand(["tag:it:alpha"], kinds=["any"], max_nodes=3)
    assert store.expand(["tag:it:alpha"], kinds=[], max_nodes=3)


def test_inmemory_expansion_legacy_direction_values_map_to_single_axis() -> None:
    store = InMemoryGraphStore()
    store.upsert_nodes(
        [
            {
                "node_id": f"tag:it:{name}",
                "labels": ["ContextNode"],
                "properties": {
                    "tag_id": f"tag:it:{name}",
                    "canonical_name": name,
                    "domain": "it",
                    "source_ids": ["src://a.txt"],
                    "chunk_ids": ["chk:abc"],
                },
            }
            for name in ("alpha", "beta")
        ]
    )
    store.upsert_edges(
        [
            {
                "from_id": "tag:it:alpha",
                "to_id": "tag:it:beta",
                "type": "REQUIRES_CONSTRAINT",
                "properties": {
                    "source_ids": ["src://a.txt"],
                    "chunk_ids": ["chk:abc"],
                },
            }
        ]
    )

    outgoing = store.expand(["tag:it:alpha"], direction="parent", max_nodes=3)
    incoming = store.expand(["tag:it:alpha"], direction="related", max_nodes=3)

    assert [row["node_id"] for row in outgoing] == ["tag:it:beta"]
    assert incoming == []


def test_neo4j_expansion_query_is_not_restricted_to_parent_or_related() -> None:
    store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "pass")
    session = _Session()
    store._session = lambda: session

    store.expand(["tag:it:alpha"], max_nodes=3)

    assert ":PARENT" not in session.query
    assert ":RELATED" not in session.query
    assert "-[*1..2]-" in session.query

    store.expand(["tag:it:alpha"], direction="out", kinds=["CONTRADICTS"], max_nodes=3)

    assert "MATCH path=(seed)-[:CONTRADICTS*1..2]->(node)" in session.query

    store.expand(["tag:it:alpha"], direction="in", max_nodes=3)

    assert "MATCH path=(seed)<-[*1..2]-(node)" in session.query


def test_expansion_depth_is_capped_and_shared_across_adapters() -> None:
    """Профиль может запросить больше предела, но урезание обязано быть общим и явным."""
    from graphrag_proto.retrieval.adapters.base import MAX_EXPANSION_DEPTH, _expand_depth

    assert _expand_depth(1) == 1
    assert _expand_depth(MAX_EXPANSION_DEPTH) == MAX_EXPANSION_DEPTH
    assert _expand_depth(MAX_EXPANSION_DEPTH + 5) == MAX_EXPANSION_DEPTH
    assert _expand_depth(0) == 1
    assert _expand_depth(-7) == 1

    store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "pass")
    session = _Session()
    store._session = lambda: session

    store.expand(["tag:it:alpha"], max_depth=99, max_nodes=3)

    assert f"1..{MAX_EXPANSION_DEPTH}]" in session.query
    assert "1..99" not in session.query


def test_build_sources_graph_relevance_is_overridable() -> None:
    """Relevance графовой оси — параметр, а не зашитое 1.0 (docs/04, разделение домов)."""
    from graphrag_proto.retrieval.pipeline import GRAPH_SOURCE_RELEVANCE, build_sources

    chunks = [{"source_url": "s://a.txt", "chunk_id": "chk:1", "score": 0.9}]
    skeleton = [{"n": {"node_id": "tag:it:x", "source_ids": ["s://b.txt"]}}]

    default = build_sources(chunks, skeleton)
    tuned = build_sources(chunks, skeleton, graph_relevance=0.25)

    assert GRAPH_SOURCE_RELEVANCE == 1.0
    assert {row["source_url"]: row["relevance"] for row in default}["s://b.txt"] == 1.0
    assert {row["source_url"]: row["relevance"] for row in tuned}["s://b.txt"] == 0.25
    assert {row["source_url"] for row in tuned} == {"s://a.txt", "s://b.txt"}


def test_expansion_depth_cap_is_identical_in_both_adapters() -> None:
    """Предел глубины обязан действовать одинаково: иначе стенд и тесты дают разный граф.

    Регрессия: `MAX_EXPANSION_DEPTH` применялся только в neo4j.py, а inmemory.py
    использовал сырое `max_depth`. Из-за этого in-memory путь, на котором держатся
    юнит-тесты, не был защищён пределом.
    """
    from graphrag_proto.retrieval.adapters.base import MAX_EXPANSION_DEPTH, _expand_depth

    store = InMemoryGraphStore()
    # Цепочка ДЛИННЕЕ потолка, и это условие проверяется явно. Раньше цепочка была из
    # 5 узлов, а потолок 3, и тест проходил; после подъёма потолка до 6 цепочка стала
    # короче него, ограничителем перестал быть потолок - и тест стал проходить даже с
    # ПОЛНОСТЬЮ отключённым ограничением, то есть проверял уже не то. Теперь любой
    # потолок снова упирается в цепочку, и проверка не может выродиться незаметно.
    chain_length = MAX_EXPANSION_DEPTH + 2
    assert chain_length > MAX_EXPANSION_DEPTH
    store.upsert_nodes(
        [
            {
                "node_id": f"tag:it:{name}",
                "labels": ["ContextNode"],
                "properties": {
                    "tag_id": f"tag:it:{name}",
                    "canonical_name": name,
                    "domain": "it",
                    "source_ids": ["src://a.txt"],
                    "chunk_ids": ["chk:abc"],
                },
            }
            for name in (f"n{index}" for index in range(chain_length))
        ]
    )
    store.upsert_edges(
        [
            {
                "from_id": f"tag:it:n{index}",
                "to_id": f"tag:it:n{index + 1}",
                "type": "REFERENCES",
                "properties": {
                    "source_ids": ["src://a.txt"],
                    "chunk_ids": ["chk:abc"],
                },
            }
            for index in range(chain_length - 1)
        ]
    )

    beyond_cap = MAX_EXPANSION_DEPTH + 10
    rows = store.expand(["tag:it:n0"], max_depth=beyond_cap, max_nodes=chain_length * 4)

    assert rows, "цепочка рёбер должна находиться"
    assert max(row["depth"] for row in rows) == MAX_EXPANSION_DEPTH
    assert len(rows) == MAX_EXPANSION_DEPTH
    assert _expand_depth(beyond_cap) == MAX_EXPANSION_DEPTH
    # Глубина 6 обязана быть достижима: иначе «потолок 6» ничего не разрешает и
    # документированная партономия (5 хопов от дома до страны) остаётся недостижимой.
    assert _expand_depth(MAX_EXPANSION_DEPTH) == 6


def test_remove_source_clears_edge_provenance() -> None:
    """Снятие источника чистит опору, но НЕ создаёт её там, где её не было.

    Разделение существенно. Связь, у которой опора была и осталась пустой, обязана стать
    кандидатом на уборку - это и есть ретракция утверждения. А связь, у которой опоры не
    было никогда (пользовательское утверждение, структурная связь), обязана её не получить:
    появление `chunk_ids = []` превращало нетронутую связь в кандидата, то есть обновление
    документа само порождало мусор, который затем съедала уборка.

    Прежняя версия теста проверяла только первый случай и проходила на втором случайно:
    у ребра фикстуры `chunk_ids` не было, код его создавал, и проверка видела ожидаемое
    `[]`. То есть тест закреплял поведение, которое и было дырой.
    """
    store = InMemoryGraphStore()
    _seed(store)
    # Свои идентификаторы, а не узлы фикстуры: у фикстуры уже есть ребро с `origin: user`
    # на ключе (database, storage, RELATED), и `upsert_edges` сольётся с ним, сохранив
    # ручное происхождение и выбросив `chunk_ids` из входящих свойств. Тест на ретракцию
    # не должен зависеть от чужого приоритета слияния.
    with_support = ("tag:it:probe_a", "tag:it:probe_b", "PROBE_WITH_SUPPORT")
    without_support = ("tag:it:probe_c", "tag:it:probe_d", "PROBE_WITHOUT_SUPPORT")
    store.upsert_edges(
        [
            {
                "from_id": "tag:it:probe_a",
                "to_id": "tag:it:probe_b",
                "type": "PROBE_WITH_SUPPORT",
                "properties": {
                    "source_ids": ["src://a.txt"],
                    "chunk_ids": ["chk:abc"],
                },
            },
            {
                "from_id": "tag:it:probe_c",
                "to_id": "tag:it:probe_d",
                "type": "PROBE_WITHOUT_SUPPORT",
                "properties": {"source_ids": ["src://a.txt"], "scope": "user"},
            },
        ]
    )
    assert store._edges[with_support]["chunk_ids"] == ["chk:abc"]
    assert "chunk_ids" not in store._edges[without_support]

    store.remove_source_from_entities("it", "src://a.txt", ["chk:abc"])

    # Опора была и стала пустой - это кандидат, и так должно быть.
    assert store._edges[with_support]["source_ids"] == []
    assert store._edges[with_support].get("chunk_ids") == []
    # Связи без опоры снятие источника не должно награждать пустым списком.
    assert "chunk_ids" not in store._edges[without_support], (
        "связи без опоры создано chunk_ids: [] - она стала кандидатом на уборку, "
        "хотя документ её не утверждал"
    )


def test_inmemory_context_expansion_without_seeds_returns_empty() -> None:
    store = InMemoryGraphStore()
    _seed(store)

    assert store.expand([], max_nodes=3) == []
    assert store.expand(["tag:it:missing"], max_nodes=3) == []


def test_graph_transaction_commit_applies_nodes_and_edges_atomically() -> None:
    store = InMemoryGraphStore()
    with store.transaction() as tx:
        tx.upsert_nodes(
            [
                {
                    "node_id": "tag:it:database",
                    "labels": ["ContextNode"],
                    "properties": {"tag_id": "tag:it:database", "domain": "it"},
                },
                {
                    "node_id": "tag:it:storage",
                    "labels": ["ContextNode"],
                    "properties": {"tag_id": "tag:it:storage", "domain": "it"},
                },
            ]
        )
        tx.upsert_edges(
            [
                {
                    "from_id": "tag:it:database",
                    "to_id": "tag:it:storage",
                    "type": "RELATED",
                    "properties": {"kind": "parent"},
                }
            ]
        )

    rows = store.expand(
        ["tag:it:database"],
        direction="parent",
        max_depth=1,
        max_fanout=1,
        max_nodes=1,
    )

    assert store.get_node("tag:it:database") is not None
    assert store.get_node("tag:it:storage") is not None
    assert [row["node_id"] for row in rows] == ["tag:it:storage"]


def test_graph_transaction_rollback_on_error() -> None:
    store = InMemoryGraphStore()

    with pytest.raises(RuntimeError, match="сбой в середине"), store.transaction() as tx:
            tx.upsert_nodes(
                [
                    {
                        "node_id": "tag:it:database",
                        "labels": ["ContextNode"],
                        "properties": {"tag_id": "tag:it:database"},
                    }
                ]
            )
            tx.upsert_nodes(
                [
                    {
                        "node_id": "tag:it:storage",
                        "labels": ["ContextNode"],
                        "properties": {"tag_id": "tag:it:storage"},
                    }
                ]
            )
            raise RuntimeError("сбой в середине")

    assert store.get_node("tag:it:database") is None
    assert store.get_node("tag:it:storage") is None


def test_delete_context_node_removes_incident_links() -> None:
    store = InMemoryGraphStore()
    _seed(store)

    assert store.delete_node("tag:it:storage") is True
    assert store.expand(["tag:it:database"], max_nodes=3) == []
    assert store.get_node("tag:it:storage") is None
    assert store.delete_node("tag:it:storage") is False


def test_list_chunk_ids_of_source_returns_contains() -> None:
    store = InMemoryGraphStore()
    _seed(store)

    assert store.list_chunk_ids_of_source("src:it:src://a.txt") == ["chk:abc"]


def test_remove_source_clears_context_node_provenance() -> None:
    store = InMemoryGraphStore()
    _seed(store)

    store.remove_source_from_entities("it", "src://a.txt", ["chk:abc"])

    for node_id in ("tag:it:database", "tag:it:storage"):
        node = store.get_node(node_id)
        assert node is not None
        assert node["source_ids"] == []
        assert node["chunk_ids"] == []


def test_vector_store_search_and_delete() -> None:
    store = InMemoryVectorStore()
    embedding = deterministic_embedding("База данных запрос")
    store.upsert_vectors(
        [
            {
                "chunk_id": "chk:1",
                "embedding": embedding,
                "metadata": {
                    "text": "База данных запрос",
                    "source_url": "src://1",
                },
            },
            {
                "chunk_id": "chk:2",
                "embedding": deterministic_embedding("другая тема"),
                "metadata": {"text": "другая тема", "source_url": "src://2"},
            },
        ]
    )

    hits = store.vector_search(embedding, top_k=1)

    assert len(hits) == 1
    assert hits[0]["chunk_id"] == "chk:1"
    assert abs(hits[0]["score"] - 1.0) < 1e-3

    store.delete_vectors(["chk:1"])
    assert "chk:1" not in [hit["chunk_id"] for hit in store.vector_search(embedding, top_k=5)]


def test_vector_store_filters_domain() -> None:
    store = InMemoryVectorStore()
    embedding = deterministic_embedding("одинаковый текст")
    store.upsert_vectors(
        [
            {
                "chunk_id": "chk:it",
                "embedding": embedding,
                "metadata": {
                    "text": "одинаковый текст",
                    "source_url": "src://it",
                    "domain": "it",
                },
            },
            {
                "chunk_id": "chk:library",
                "embedding": embedding,
                "metadata": {
                    "text": "одинаковый текст",
                    "source_url": "src://library",
                    "domain": "library",
                },
            },
        ]
    )

    assert [hit["chunk_id"] for hit in store.vector_search(embedding, domain="it")] == [
        "chk:it"
    ]


class _Node:
    labels = ("ContextNode",)
    element_id = "node-1"

    def __init__(self, properties: dict[str, object]) -> None:
        self._properties = properties

    def __iter__(self):
        return iter(self._properties)

    def items(self):
        return self._properties.items()

    def __getitem__(self, key: str) -> object:
        return self._properties[key]

    def get(self, key: str, default: object = None) -> object:
        return self._properties.get(key, default)


def test_normalize_vector_row_preserves_generic_context_metadata() -> None:
    result = normalize_vector_row(
        {
            "chunk_id": "chk:1",
            "score": 0.5,
            "c": _Node(
                {
                    "text": "текст",
                    "source_url": "src://a",
                    "domain": "it",
                    "context_ids": ["tag:it:database"],
                    "tag_ids": ["tag:it:database"],
                    "custom": {"language": "ru"},
                }
            ),
        }
    )

    assert result == {
        "chunk_id": "chk:1",
        "score": 0.5,
        "text": "текст",
        "source_url": "src://a",
        "domain": "it",
        "context_ids": ["tag:it:database"],
        "tag_ids": ["tag:it:database"],
        "custom": {"language": "ru"},
    }


class _Result:
    def data(self) -> list[dict[str, object]]:
        return [
            {
                "chunk_id": "chk:it",
                "embedding": [1.0],
                "c": _Node(
                    {
                        "text": "текст",
                        "source_url": "src://a",
                        "domain": "it",
                        "context_ids": ["tag:it:database"],
                        "custom": {"language": "ru"},
                    }
                ),
            }
        ]


class _Session:
    def __init__(self) -> None:
        self.query = ""
        self.parameters: dict[str, object] = {}

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def run(self, query: str, parameters: dict[str, object]) -> _Result:
        self.query = query
        self.parameters = parameters
        return _Result()


def test_neo4j_vector_search_filters_domain_and_normalizes_context_metadata() -> None:
    store = Neo4jVectorStore("bolt://localhost:7687", "neo4j", "pass")
    session = _Session()
    store._session = lambda: session

    result = store.vector_search([1.0], domain="it")

    assert "c.domain = $domain" in session.query
    assert session.parameters == {"domain": "it"}
    assert result == [
        {
            "chunk_id": "chk:it",
            "score": 1.0,
            "text": "текст",
            "source_url": "src://a",
            "domain": "it",
            "context_ids": ["tag:it:database"],
            "custom": {"language": "ru"},
        }
    ]


class _LegacyVectorStore(VectorStoreProvider):
    def vector_search(
        self,
        embedding: list[float],
        top_k: int = 5,
    ) -> list[dict[str, object]]:
        rows = [
            {
                "chunk_id": f"library-{index}",
                "score": 1.0,
                "metadata": {
                    "domain": "library",
                    "text": "текст",
                    "source_url": f"src://library-{index}",
                },
            }
            for index in range(10)
        ]
        rows.append(
            {
                "chunk_id": "it",
                "score": 1.0,
                "metadata": {
                    "domain": "it",
                    "text": "текст",
                    "source_url": "src://it",
                },
            }
        )
        return rows[:top_k]

    def upsert_vectors(self, items: list[dict[str, object]]) -> None:
        return None

    def delete_vectors(self, chunk_ids: list[str]) -> None:
        return None


def test_vector_retriever_rejects_legacy_store_without_domain_keyword() -> None:
    retriever = VectorRetriever(_LegacyVectorStore(), top_k=5, domain="it")

    with pytest.raises(NotImplementedError):
        retriever.retrieve([1.0])


def test_vector_transaction_rollback() -> None:
    store = InMemoryVectorStore()
    embedding = deterministic_embedding("query")

    with pytest.raises(ValueError, match="откат"), store.transaction() as tx:
            tx.upsert_vectors(
                [
                    {
                        "chunk_id": "chk:x",
                        "embedding": embedding,
                        "metadata": {},
                    }
                ]
            )
            raise ValueError("откат")

    assert store.vector_search(embedding, top_k=5) == []


def test_noop_reranker_keeps_scores() -> None:
    chunks = [{"score": 0.9}, {"score": 0.1}, {}]

    assert NoOpRerankerAdapter().rerank("q", chunks) == [0.9, 0.1, 0.0]


def test_fake_llm_deltas_and_full_text() -> None:
    llm = FakeLLM(text="граф знаний data store")
    parts = list(llm.generate("prompt"))

    assert "".join(parts) == "граф знаний data store"
    assert llm.full_text == "граф знаний data store"


def test_openai_adapter_http_connect_error_is_runtime() -> None:
    from graphrag_proto.retrieval.adapters.llm import OpenAICompatibleAdapter

    adapter = OpenAICompatibleAdapter(model="test-model", base_url="http://127.0.0.1:1", timeout_s=0.2)

    with pytest.raises(RuntimeError):
        list(adapter.generate("hi"))


def test_neo4j_fanout_is_counted_per_branch_point_not_per_seed() -> None:
    """Ветвление ограничивается ПО УЗЛУ, как в in-memory, а не суммарно на старт.

    Различие не косметическое. При счёте по seed ограничение съедалось ближайшими
    строками: на глубине больше 1 промежуточный узел разветвлялся как угодно, и глубина
    почти не добавляла ответа. При счёте по узлу 8 - это «не больше 8 соседей от каждого
    узла», то есть ровно то, что делает in-memory, и параметр наконец значит одно и то же
    в обоих адаптерах.
    """
    rows = [
        # Три соседа n0: при лимите 2 третьй отбрасывается.
        {"path": ["tag:it:n0", "tag:it:a1"], "node": {"node_id": "tag:it:a1"}, "depth": 1},
        {"path": ["tag:it:n0", "tag:it:a2"], "node": {"node_id": "tag:it:a2"}, "depth": 1},
        {"path": ["tag:it:n0", "tag:it:a3"], "node": {"node_id": "tag:it:a3"}, "depth": 1},
        # По одному ребру из a1 и a2: лимит считается от КАЖДОГО из них, а не от n0,
        # поэтому при счёте по seed эти строки были бы отброшены как третьи и четвёртые.
        {
            "path": ["tag:it:n0", "tag:it:a1", "tag:it:b1"],
            "node": {"node_id": "tag:it:b1"},
            "depth": 2,
        },
        {
            "path": ["tag:it:n0", "tag:it:a2", "tag:it:b2"],
            "node": {"node_id": "tag:it:b2"},
            "depth": 2,
        },
    ]

    class _Rows:
        def data(self) -> list[dict[str, object]]:
            return rows

    class _RowsSession:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        def run(self, query: str, parameters: dict[str, object]) -> _Rows:
            return _Rows()

    store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "pass")
    store._session = lambda: _RowsSession()  # type: ignore[assignment]

    found = store.expand(["tag:it:n0"], max_depth=2, max_fanout=2, max_nodes=10)
    ids = [row["node_id"] for row in found]

    assert ids == ["tag:it:a1", "tag:it:a2", "tag:it:b1", "tag:it:b2"], ids
    assert "tag:it:a3" not in ids, "третий сосед n0 превышает лимит ветвления"
    assert "tag:it:b1" in ids, "ребро из a1 не должно съедать лимит n0"


def test_neo4j_fanout_limit_applies_per_node_on_one_chain() -> None:
    """На цепочке лимит 1 - это один сосед от КАЖДОГО узла, а не одна строка на старт.

    Контраст с прежним счётом по seed резкий: при счёте по seed из цепочки
    n0 -> n1 -> n2 прошла бы только n1, и глубина 2 была бы недостижима в принципе.
    """
    rows = [
        {
            "path": ["tag:it:n0", "tag:it:n1"],
            "node": {"node_id": "tag:it:n1"},
            "depth": 1,
        },
        {
            "path": ["tag:it:n0", "tag:it:n1", "tag:it:n2"],
            "node": {"node_id": "tag:it:n2"},
            "depth": 2,
        },
    ]

    class _Rows:
        def data(self) -> list[dict[str, object]]:
            return rows

    class _RowsSession:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        def run(self, query: str, parameters: dict[str, object]) -> _Rows:
            return _Rows()

    store = Neo4jGraphStore("bolt://localhost:7687", "neo4j", "pass")
    store._session = lambda: _RowsSession()  # type: ignore[assignment]

    found = store.expand(["tag:it:n0"], max_depth=2, max_fanout=1, max_nodes=10)
    assert [row["node_id"] for row in found] == ["tag:it:n1", "tag:it:n2"]
