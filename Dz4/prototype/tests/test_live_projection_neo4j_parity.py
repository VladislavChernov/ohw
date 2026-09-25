"""Live Neo4j parity для offline projection (LP-13).

Полный обычный suite работает на InMemory; этот файл — единственная проверка
того, что тот же projection job даёт тот же результат на живом Neo4j: Source/Chunk
anchors, `CONTAINS`-связи, provenance, vector metadata backfill, `verify_projection`
и retention с domain-фильтром.

Запуск (только при поднятом Neo4j):

    NEO4J_LIVE_URI=bolt://localhost:7687 NEO4J_LIVE_PASSWORD=graphrag \
      uv run pytest -m live tests/test_live_projection_neo4j_parity.py

Без `NEO4J_LIVE_URI` тест пропускается, поэтому в обычный `pytest -q` не попадает.
Данные изолированы префиксом `lp13://` и удаляются в `finally`.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import pytest

from graphrag_proto.ingestion_service.document import Document
from graphrag_proto.ingestion_service.projection import (
    InMemoryProjectionStateStore,
    OfflineProjectionJob,
    ProjectionState,
    ProjectionUnit,
)
from graphrag_proto.ingestion_service.storage.registry import DocumentRegistry
from graphrag_proto.retrieval.adapters.deterministic import deterministic_embedding
from graphrag_proto.retrieval.adapters.inmemory import (
    InMemoryGraphStore,
    InMemoryVectorStore,
)
from graphrag_proto.retrieval.adapters.neo4j import Neo4jGraphStore, Neo4jVectorStore

pytestmark = pytest.mark.live

# Отдельный домен, чтобы verify_projection не видел Chunk-узлы других
# (в т.ч. ранее залитых демо-данных) с чужим projection_revision.
_DOMAIN = "lp13"


def _live_uri() -> str:
    return os.environ.get("NEO4J_LIVE_URI", "")


def _live_password() -> str:
    return os.environ.get("NEO4J_LIVE_PASSWORD", "graphrag")


@pytest.fixture()
def live_pair() -> tuple[Neo4jGraphStore, Neo4jVectorStore]:
    if not _live_uri():
        pytest.skip("NEO4J_LIVE_URI не задан: live Neo4j parity пропускается")
    graph = Neo4jGraphStore(_live_uri(), "neo4j", _live_password())
    vector = Neo4jVectorStore(_live_uri(), "neo4j", _live_password())
    try:
        graph.query("RETURN 1 AS ok")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Neo4j недоступен по {_live_uri()}: {type(exc).__name__}")
    yield graph, vector
    # Чистим строго по префиксу lp13:// — префикс есть и в source_url, и в
    # производных node_id (chk:lp13://…, src:lp13:lp13://…, tag:lp13:lp13://…),
    # поэтому фильтр по вхождению, а не по node_id STARTS WITH. Домен теста
    # изолирован (lp13), а чужие узлы/связи не трогаются.
    graph.query("MATCH (n) WHERE n.node_id CONTAINS 'lp13://' DETACH DELETE n")
    graph.query(
        "MATCH ()-[r]->() WHERE any(value IN coalesce(r.source_ids, []) "
        "WHERE value CONTAINS 'lp13://') DELETE r"
    )


def _unit(source_url: str) -> ProjectionUnit:
    chunk_id = f"chk:{source_url}"
    tag_id = f"tag:{_DOMAIN}:{source_url}"
    return ProjectionUnit(
        source_url=source_url,
        nodes=[
            {
                "node_id": f"src:{_DOMAIN}:{source_url}",
                "labels": ["Source"],
                "properties": {"source_url": source_url, "domain": _DOMAIN},
            },
            {
                "node_id": chunk_id,
                "labels": ["Chunk"],
                "properties": {
                    "chunk_id": chunk_id,
                    "source_url": source_url,
                    "domain": _DOMAIN,
                    "text": "живой текст",
                },
            },
            {
                "node_id": tag_id,
                "labels": ["Tag"],
                "properties": {
                    "canonical_name": source_url,
                    "path": [tag_id],
                    "kind": "keyword",
                    "origin": "ai",
                    "confidence": 0.9,
                    "domain": _DOMAIN,
                    "source_ids": [source_url],
                    "chunk_ids": [chunk_id],
                },
            },
        ],
        edges=[
            {
                "from_id": f"src:{_DOMAIN}:{source_url}",
                "to_id": chunk_id,
                "type": "CONTAINS",
                "properties": {
                    "domain": _DOMAIN,
                    "source_ids": [source_url],
                    "chunk_ids": [chunk_id],
                },
            }
        ],
        vector_metadata=[
            {
                "chunk_id": chunk_id,
                "metadata": {
                    "context_ids": [tag_id],
                    "tag_ids": [tag_id],
                },
            }
        ],
    )


def _job(
    graph: Any,
    vector: Any,
    source: str,
    registry: DocumentRegistry | None = None,
    include_unit: bool = True,
) -> OfflineProjectionJob:
    unit = _unit(source)
    return OfflineProjectionJob(
        state_store=InMemoryProjectionStateStore(),
        graph_store=graph,
        vector_store=vector,
        source_provider=lambda _domain, _revision: [unit] if include_unit else [],
        registry=registry,
    )


def test_live_projection_writes_anchors_and_backfills_vectors(
    live_pair: tuple[Neo4jGraphStore, Neo4jVectorStore],
) -> None:
    graph, vector = live_pair
    source = f"lp13://doc/{uuid.uuid4().hex}"

    state = _job(graph, vector, source).run(_DOMAIN, "live-1")

    assert state.status == "ready", state.last_error
    chunk_id = f"chk:{source}"
    tag_id = f"tag:{_DOMAIN}:{source}"
    chunk = graph.get_node(chunk_id)
    assert chunk is not None and chunk["domain"] == _DOMAIN
    assert chunk["source_url"] == source
    assert graph.get_node(f"src:{_DOMAIN}:{source}") is not None
    assert graph.verify_edge(f"src:{_DOMAIN}:{source}", chunk_id, "CONTAINS") is True
    tag = graph.get_node(tag_id)
    assert tag is not None and tag["source_ids"] == [source]
    metadata = vector.get_vector_metadata(chunk_id)
    assert metadata is not None
    assert metadata["context_ids"] == [tag_id]
    assert metadata["projection_revision"] == state.projection_revision
    assert vector.verify_projection(_DOMAIN, state.projection_revision) is True


def test_live_projection_is_idempotent_and_does_not_duplicate_anchors(
    live_pair: tuple[Neo4jGraphStore, Neo4jVectorStore],
) -> None:
    graph, vector = live_pair
    source = f"lp13://doc/{uuid.uuid4().hex}"
    state_store = InMemoryProjectionStateStore()
    unit = _unit(source)

    def build() -> OfflineProjectionJob:
        return OfflineProjectionJob(
            state_store=state_store,
            graph_store=graph,
            vector_store=vector,
            source_provider=lambda _domain, _revision: [unit],
        )

    first = build().run(_DOMAIN, "live-2")
    second = build().run(_DOMAIN, "live-2")

    assert first.status == "ready", first.last_error
    assert second.status == "ready"
    assert second.projection_revision == first.projection_revision
    assert second.job_id == first.job_id
    rows = graph.query(
        "MATCH (n {node_id: $node_id}) RETURN count(n) AS c",
        {"node_id": f"chk:{source}"},
    )
    assert int(rows[0]["c"]) == 1
    edges = graph.query(
        "MATCH (:Source {node_id: $from_id})-[r:CONTAINS]->(:Chunk {node_id: $to_id}) "
        "RETURN count(r) AS c",
        {"from_id": f"src:{_DOMAIN}:{source}", "to_id": f"chk:{source}"},
    )
    assert int(edges[0]["c"]) == 1


def test_live_projection_retention_cleans_graph_and_vectors(
    live_pair: tuple[Neo4jGraphStore, Neo4jVectorStore],
) -> None:
    graph, vector = live_pair
    source = f"lp13://doc/{uuid.uuid4().hex}"
    registry = DocumentRegistry(Path(os.environ.get("LP13_REGISTRY", "runtime/lp13.db")))
    registry.upsert(
        Document(
            source_id="",
            source_url=source,
            domain=_DOMAIN,
            doc_type="txt",
            content_hash="live-hash",
        )
    )
    _job(graph, vector, source, registry).run(_DOMAIN, "live-3")
    assert graph.get_node(f"chk:{source}") is not None

    # Документ soft-deleted в registry: source больше не active и в units
    # больше не приходит — только в stale_source_urls.
    registry.soft_delete(_DOMAIN, source)
    state = _job(graph, vector, source, registry, include_unit=False).run(
        _DOMAIN,
        "live-4",
        stale_source_urls=[source],
    )

    assert state.status == "ready", state.last_error
    assert state.skipped_source_count == 1
    assert graph.get_node(f"chk:{source}") is None
    assert graph.get_node(f"src:{_DOMAIN}:{source}") is None
    tag = graph.get_node(f"tag:{_DOMAIN}:{source}")
    assert tag is not None and tag["source_ids"] == []
    assert vector.get_vector_metadata(f"chk:{source}") is None
    edges = graph.query(
        "MATCH (:Source {node_id: $from_id})-[r:CONTAINS]->(:Chunk {node_id: $to_id}) "
        "RETURN count(r) AS c",
        {"from_id": f"src:{_DOMAIN}:{source}", "to_id": f"chk:{source}"},
    )
    assert int(edges[0]["c"]) == 0
    audit_events = [
        event
        for event in _job(graph, vector, source, registry, include_unit=False).audit_log
    ]
    assert audit_events == []


def test_live_projection_retention_refuses_active_source(
    live_pair: tuple[Neo4jGraphStore, Neo4jVectorStore],
) -> None:
    graph, vector = live_pair
    source = f"lp13://doc/{uuid.uuid4().hex}"
    registry = DocumentRegistry(Path(os.environ.get("LP13_REGISTRY", "runtime/lp13.db")))
    registry.upsert(
        Document(
            source_id="",
            source_url=source,
            domain=_DOMAIN,
            doc_type="txt",
            content_hash="live-hash-active",
        )
    )
    _job(graph, vector, source, registry).run(_DOMAIN, "live-5")

    state = _job(graph, vector, source, registry, include_unit=False).run(
        _DOMAIN,
        "live-6",
        stale_source_urls=[source],
    )

    assert state.status == "failed"
    assert "active" in (state.last_error or "")
    assert graph.get_node(f"chk:{source}") is not None
    assert vector.get_vector_metadata(f"chk:{source}") is not None


def test_live_projection_state_store_persists_ready(
    live_pair: tuple[Neo4jGraphStore, Neo4jVectorStore],
    tmp_path: Path,
) -> None:
    from graphrag_proto.ingestion_service.projection import SQLiteProjectionStateStore

    graph, vector = live_pair
    source = f"lp13://doc/{uuid.uuid4().hex}"
    state_store = SQLiteProjectionStateStore(tmp_path / "projection-live.db")
    unit = _unit(source)
    job = OfflineProjectionJob(
        state_store=state_store,
        graph_store=graph,
        vector_store=vector,
        source_provider=lambda _domain, _revision: [unit],
    )

    state = job.run(_DOMAIN, "live-7")

    assert state.status == "ready", state.last_error
    stored = SQLiteProjectionStateStore(tmp_path / "projection-live.db").get(_DOMAIN)
    assert stored is not None
    assert stored.status == "ready"
    assert stored.projection_revision == state.projection_revision
    assert state_store.get(_DOMAIN) == stored
    assert isinstance(state, ProjectionState)
    assert vector.verify_projection(_DOMAIN, stored.projection_revision) is True
    assert deterministic_embedding("живой текст")
    assert InMemoryGraphStore() is not graph
    assert InMemoryVectorStore() is not vector
