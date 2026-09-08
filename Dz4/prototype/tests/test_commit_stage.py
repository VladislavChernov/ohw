"""COMMIT (L2-04/L2-05): запись в провайдеры, атомарность, идемпотентность, soft-delete."""

from __future__ import annotations

from pathlib import Path

from graphrag_proto.ingestion_service.pipeline.orchestrator import (
    Analyzer,
    ChunkStage,
    CommitStage,
    ContractStage,
    DedupStage,
    EmbedStage,
    ExtractStage,
    IngestStage,
    NormalizeStage,
    PipelineContext,
    ValidateStage,
    _chunk_id,
    _entity_node_id,
    _source_node_id,
    soft_delete_source,
)
from graphrag_proto.ingestion_service.readers.registry import TxtReader
from graphrag_proto.ingestion_service.storage.registry import DocumentRegistry
from graphrag_proto.retrieval.adapters.deterministic import deterministic_embedding
from graphrag_proto.retrieval.adapters.inmemory import InMemoryGraphStore, InMemoryVectorStore

CONTENT_1 = "кэширование данные дедупликация алгоритм базы данных\n"
CONTENT_2 = "индекс поиск дедупликация граф знаний через рёбра и вершины\n"


def build_analyzer(tmp_path: Path, graph, vector) -> Analyzer:
    registry = DocumentRegistry(tmp_path / "commit.db")
    return Analyzer(
        [
            IngestStage({"txt": TxtReader()}),
            ChunkStage(),
            EmbedStage(),
            ExtractStage(),
            NormalizeStage(""),
            DedupStage(),
            ContractStage(),
            ValidateStage(),
            CommitStage(registry, graph_store=graph, vector_store=vector),
        ]
    ), registry


def run_source(analyzer: Analyzer, src: Path) -> PipelineContext:
    ctx = PipelineContext(job_id="j", domain="it", doc_type="txt", source_url="src://d.txt", source_path=str(src))
    analyzer.run(ctx)
    return ctx


def test_commit_writes_entities_edges_vectors(tmp_path: Path) -> None:
    graph, vector = InMemoryGraphStore(), InMemoryVectorStore()
    analyzer, _ = build_analyzer(tmp_path, graph, vector)
    src = tmp_path / "d.txt"
    src.write_text(CONTENT_1, encoding="utf-8")
    ctx = run_source(analyzer, src)

    source_id = _source_node_id("it", "src://d.txt")
    assert ctx.commit_applied is True
    assert graph.get_node(source_id) is not None
    assert graph.list_chunk_ids_of_source(source_id)

    chunk_ids = graph.list_chunk_ids_of_source(source_id)
    chunks = {cid: graph.get_node(cid) for cid in chunk_ids}
    assert all(chunks[c]["_labels"] == ["Chunk"] for c in chunks)

    entity_node = graph.get_node(_entity_node_id("it", "дедупликация"))
    assert entity_node is not None
    assert entity_node["_labels"] == ["Entity"]
    assert entity_node["extractor_version"] == "deterministic:v1"
    assert entity_node["source_ids"] == ["src://d.txt"]

    chunk_text = graph.get_node(chunk_ids[0])["text"]
    hits = vector.vector_search(deterministic_embedding(chunk_text), top_k=10)
    assert hits, "эмбеддинги чанков должны участвовать в поиске"


def test_commit_idempotent_noop(tmp_path: Path) -> None:
    graph, vector = InMemoryGraphStore(), InMemoryVectorStore()
    analyzer, registry = build_analyzer(tmp_path, graph, vector)
    src = tmp_path / "d.txt"
    src.write_text(CONTENT_1, encoding="utf-8")

    run_source(analyzer, src)
    before_ids = graph.list_chunk_ids_of_source(_source_node_id("it", "src://d.txt"))
    vectors_before = list(vector._vectors.keys())

    run_source(analyzer, src)
    latest = registry.latest_active("it", "src://d.txt")
    assert latest is not None and latest["version"] == 1
    assert graph.list_chunk_ids_of_source(_source_node_id("it", "src://d.txt")) == before_ids
    assert set(vector._vectors.keys()) == set(vectors_before)


def test_commit_reindex_replaces_stale_chunks(tmp_path: Path) -> None:
    graph, vector = InMemoryGraphStore(), InMemoryVectorStore()
    analyzer, registry = build_analyzer(tmp_path, graph, vector)
    src = tmp_path / "d.txt"

    src.write_text(CONTENT_1, encoding="utf-8")
    run_source(analyzer, src)
    chunk_ids = graph.list_chunk_ids_of_source(_source_node_id("it", "src://d.txt"))

    src.write_text(CONTENT_2, encoding="utf-8")
    run_source(analyzer, src)

    latest = registry.latest_active("it", "src://d.txt")
    assert latest is not None and latest["version"] == 2
    # идентификаторы чанков стабильны (source_url+index), контент перезаписан
    assert graph.list_chunk_ids_of_source(_source_node_id("it", "src://d.txt")) == chunk_ids
    assert len(vector._vectors) == len(chunk_ids)
    for chunk_id in chunk_ids:
        assert "граф знаний" in graph.get_node(chunk_id)["text"]


def test_commit_transaction_rollback_on_error(tmp_path: Path) -> None:
    src = tmp_path / "d.txt"
    src.write_text(CONTENT_1, encoding="utf-8")

    class ExplodingVector(InMemoryVectorStore):
        def upsert_vectors(self, items) -> None:
            raise RuntimeError("сбой записи векторов")

    graph2 = InMemoryGraphStore()
    analyzer2, _ = build_analyzer(tmp_path, graph2, ExplodingVector())
    try:
        analyzer2.run(
            PipelineContext(job_id="j2", domain="it", doc_type="txt", source_url="src://d.txt", source_path=str(src))
        )
        assert False, "сбой эмбеддингов должен привести к ошибке пайплайна"
    except RuntimeError:
        pass
    # атомарность: узлы графа не записались, несмотря на то что вектор-ось упала
    assert graph2.get_node(_source_node_id("it", "src://d.txt")) is None


def test_soft_delete_source_removes_chunks_keeps_entities(tmp_path: Path) -> None:
    graph, vector = InMemoryGraphStore(), InMemoryVectorStore()
    analyzer, registry = build_analyzer(tmp_path, graph, vector)
    src = tmp_path / "d.txt"
    src.write_text(CONTENT_1, encoding="utf-8")
    run_source(analyzer, src)

    source_id = _source_node_id("it", "src://d.txt")
    chunk_ids = graph.list_chunk_ids_of_source(source_id)
    assert chunk_ids

    assert soft_delete_source(registry, graph, vector, "it", "src://d.txt") is True
    assert registry.latest_active("it", "src://d.txt") is None
    assert graph.list_chunk_ids_of_source(source_id) == []
    for chunk_id in chunk_ids:
        assert graph.get_node(chunk_id) is None
        assert chunk_id not in vector._vectors
    # сущности и источник сохраняются (историчность, ADR-014)
    assert graph.get_node(source_id) is not None
    assert graph.get_node(_entity_node_id("it", "дедупликация")) is not None

    assert soft_delete_source(registry, graph, vector, "it", "src://d.txt") is False


def test_chunk_id_deterministic() -> None:
    assert _chunk_id("src://d.txt", 0) == _chunk_id("src://d.txt", 0)
    assert _chunk_id("src://d.txt", 0) != _chunk_id("src://d.txt", 1)