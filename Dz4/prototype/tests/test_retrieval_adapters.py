"""Контрактные тесты адаптеров retrieval (ADR-012): InMemory-реализации, детерминизм.

L1-02/L2-04/L2-05: атомарность транзакции (rollback при ошибке), soft-delete,
согласованный эмбеддинг, отсутствие сетевых зависимостей.
"""

from __future__ import annotations

from graphrag_proto.retrieval.adapters.deterministic import (
    DeterministicEmbedder,
    deterministic_embedding,
)
from graphrag_proto.retrieval.adapters.inmemory import InMemoryGraphStore, InMemoryVectorStore
from graphrag_proto.retrieval.adapters.llm import FakeLLM
from graphrag_proto.retrieval.adapters.reranker import NoOpRerankerAdapter

IT_PROFILE = {
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
    "ontology": {"node_types": [{"type": "Requirement"}, {"type": "Concept"}, {"type": "Contract"}]},
}


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
                "properties": {"chunk_id": "chk:abc", "text": "База данных — упорядоченный набор данных."},
            },
            {
                "node_id": "ent:база",
                "labels": ["Concept"],
                "properties": {"canonical_name": "База данных", "source_ids": ["src://a.txt"]},
            },
            {
                "node_id": "ent:sql",
                "labels": ["Concept"],
                "properties": {"canonical_name": "SQL", "source_ids": ["src://a.txt"]},
            },
        ]
    )
    graph.upsert_edges(
        [
            {"from_id": "src:it:src://a.txt", "to_id": "chk:abc", "type": "CONTAINS", "properties": {}},
            {"from_id": "ent:база", "to_id": "ent:sql", "type": "SIMILAR_TO", "properties": {}},
        ]
    )


def test_unification_комплект() -> None:
    """Один и тот же текст даёт один и тот же вектор (инг-индекс = запрос)."""
    text = "База данных"
    assert deterministic_embedding(text) == deterministic_embedding(text)
    assert deterministic_embedding(text) == DeterministicEmbedder().embed(text)
    assert len(deterministic_embedding(text)) == 8
    assert deterministic_embedding(text) != deterministic_embedding("База данных2")


def test_inmemory_graph_query_and_edges() -> None:
    store = InMemoryGraphStore()
    _seed(store)
    from graphrag_proto.retrieval.retrievers import build_cypher

    cypher, labels = build_cypher(IT_PROFILE)
    assert labels == ["Requirement", "Concept", "Contract"]
    rows = store.query(cypher, {"terms": ["база данных"], "max_nodes": 5})
    assert rows, "должны найтись узлы по термину"
    node = rows[0]["n"]
    assert node["canonical_name"] == "База данных"
    assert rows[0]["m"]["_labels"] == ["Concept"]
    assert rows[0]["rel_type"] == "SIMILAR_TO"


def test_inmemory_unsupported_cypher_raises() -> None:
    store = InMemoryGraphStore()
    _seed(store)
    try:
        store.query("MATCH (x)-[r]->(y) RETURN x")
        assert False, "неподдерживаемая грамматика должна давать NotImplementedError"
    except NotImplementedError:
        pass


def test_graph_transaction_commit_applies_atomically() -> None:
    store = InMemoryGraphStore()
    with store.transaction() as tx:
        tx.upsert_nodes([{"node_id": "a", "labels": ["Chunk"], "properties": {}}])
        tx.upsert_edges([{"from_id": "a", "to_id": "a", "type": "X", "properties": {}}])
    assert store.get_node("a") is not None


def test_graph_transaction_rollback_on_error() -> None:
    store = InMemoryGraphStore()
    try:
        with store.transaction() as tx:
            tx.upsert_nodes([{"node_id": "a", "labels": ["Chunk"], "properties": {}}])
            tx.upsert_nodes([{"node_id": "b", "labels": ["Chunk"], "properties": {}}])
            raise RuntimeError("сбой в середине")
    except RuntimeError:
        pass
    else:
        assert False, "ожидался RuntimeError"
    assert store.get_node("a") is None and store.get_node("b") is None


def test_delete_node_removes_incident_edges() -> None:
    store = InMemoryGraphStore()
    _seed(store)
    assert store.delete_node("chk:abc") is True
    assert store.list_chunk_ids_of_source("src:it:src://a.txt") == []
    assert store.get_node("chk:abc") is None
    assert store.delete_node("chk:abc") is False


def test_list_chunk_ids_of_source_returns_contains() -> None:
    store = InMemoryGraphStore()
    _seed(store)
    assert store.list_chunk_ids_of_source("src:it:src://a.txt") == ["chk:abc"]


def test_vector_store_search_and_delete() -> None:
    store = InMemoryVectorStore()
    emb = deterministic_embedding("База данных запрос")
    store.upsert_vectors(
        [
            {"chunk_id": "chk:1", "embedding": emb, "metadata": {"text": "База данных запрос", "source_url": "s://1"}},
            {
                "chunk_id": "chk:2",
                "embedding": deterministic_embedding("другая тема"),
                "metadata": {"text": "другая тема", "source_url": "s://2"},
            },
        ]
    )
    hits = store.vector_search(emb, top_k=1)
    assert len(hits) == 1
    assert hits[0]["chunk_id"] == "chk:1"
    assert abs(hits[0]["score"] - 1.0) < 1e-3

    store.delete_vectors(["chk:1"])
    assert "chk:1" not in [h["chunk_id"] for h in store.vector_search(emb, top_k=5)]


def test_vector_transaction_rollback() -> None:
    store = InMemoryVectorStore()
    emb = deterministic_embedding("query")
    try:
        with store.transaction() as tx:
            tx.upsert_vectors([{"chunk_id": "chk:x", "embedding": emb, "metadata": {}}])
            raise ValueError("откат")
    except ValueError:
        pass
    else:
        assert False, "ожидался ValueError"
    assert store.vector_search(emb, top_k=5) == []


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

    adapter = OpenAICompatibleAdapter(base_url="http://127.0.0.1:1", timeout_s=0.2)
    try:
        for _ in adapter.generate("hi"):
            pass
        assert False, "недоступный LLM должен давать RuntimeError"
    except RuntimeError:
        pass