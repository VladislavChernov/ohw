"""COMMIT (L2-04/L2-05, A-2): запись в провайдеры, атомарность, идемпотентность, soft-delete."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from graphrag_proto.ingestion_service.pipeline.orchestrator import (
    Analyzer,
    ChunkStage,
    CommitStage,
    CommitStageError,
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
from graphrag_proto.retrieval.adapters.base import Embedder
from graphrag_proto.retrieval.adapters.deterministic import deterministic_embedding
from graphrag_proto.retrieval.adapters.inmemory import InMemoryGraphStore, InMemoryVectorStore

CONTENT_1 = "кэширование данные дедупликация алгоритм базы данных\n"
CONTENT_2 = "индекс поиск дедупликация граф знаний через рёбра и вершины\n"


def build_analyzer(
    tmp_path: Path,
    graph: InMemoryGraphStore,
    vector: InMemoryVectorStore,
    embedder: Embedder | None = None,
) -> tuple[Analyzer, DocumentRegistry]:
    registry = DocumentRegistry(tmp_path / "commit.db")
    stages = [
        IngestStage({"txt": TxtReader()}),
        ChunkStage(),
        EmbedStage(embedder),
        ExtractStage(),
        NormalizeStage(""),
        DedupStage(),
        ContractStage(),
        ValidateStage(),
        CommitStage(registry, graph_store=graph, vector_store=vector),
    ]
    return Analyzer(stages), registry


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


class ReflectedEmbedder(Embedder):
    """Эмбеддер, возвращающий заданный вектор — M3.2: EMBED пишет вектор адаптера."""

    def __init__(self, vector: list[float]) -> None:
        self._vector = vector

    def embed(self, text: str, domain: str = "") -> list[float]:
        return list(self._vector)


def test_embed_stage_writes_injected_embedder_vector(tmp_path: Path) -> None:
    graph, vector = InMemoryGraphStore(), InMemoryVectorStore()
    bge_vector = [0.01 * i for i in range(1, 9)]
    analyzer, _ = build_analyzer(tmp_path, graph, vector, embedder=ReflectedEmbedder(bge_vector))
    src = tmp_path / "d.txt"
    src.write_text(CONTENT_1, encoding="utf-8")
    run_source(analyzer, src)

    source_id = _source_node_id("it", "src://d.txt")
    chunk_ids = graph.list_chunk_ids_of_source(source_id)
    assert chunk_ids
    # L2-04: в хранилище лежит именно вектор инжектированного эмбеддера
    # (тест падает, если EmbedStage вернётся к детерминированному эмбеддеру).
    for chunk_id in chunk_ids:
        assert vector._vectors[chunk_id]["embedding"] == bge_vector


# --------------------------------------------------------------------------- A-2 (ADR-024)

class FailingVectorAxis(InMemoryVectorStore):
    """Разнородная пара (best_effort-контракт): вектор-ось падает на записи."""

    def consistency_capability(self) -> str:
        return "best_effort"

    def upsert_vectors(self, items) -> None:
        raise RuntimeError("сбой записи векторов")


class FailOnSecondUpsertVector(InMemoryVectorStore):
    """Второй прогон (re-index) падает на upsert_vectors: имитирует сбой второй оси.

    Первый прогон проходит успешно, что позволяет проверить компенсацию при re-index.
    """

    def __init__(self) -> None:
        super().__init__()
        self._call_count = 0

    def consistency_capability(self) -> str:
        return "best_effort"

    def upsert_vectors(self, items) -> None:
        self._call_count += 1
        if self._call_count >= 2:
            raise RuntimeError("сбой векторов на втором прогоне (re-index)")
        super().upsert_vectors(items)


def test_best_effort_compensates_graph_on_vector_failure(tmp_path: Path) -> None:
    """A-2 (4.1): сбой второй оси в best_effort-паре → граф компенсирован,
    джоба failed с пометкой «компенсировано», Source/Entity сохраняются."""
    graph, vector = InMemoryGraphStore(), FailingVectorAxis()
    assert not _is_atomic_pair_for_test(graph, vector)
    analyzer, _ = build_analyzer(tmp_path, graph, vector)
    src = tmp_path / "d.txt"
    src.write_text(CONTENT_1, encoding="utf-8")
    source_id = _source_node_id("it", "src://d.txt")

    with pytest.raises(CommitStageError) as excinfo:
        run_source(analyzer, src)
    assert excinfo.value.compensated is True
    assert "компенсирован" in str(excinfo.value)

    # чанки компенсированы, источник/сущности сохранились (историчность, ADR-014)
    assert graph.list_chunk_ids_of_source(source_id) == []
    assert graph.get_node(source_id) is not None
    assert graph.get_node(_entity_node_id("it", "дедупликация")) is not None
    assert not vector._vectors


def _is_atomic_pair_for_test(graph: InMemoryGraphStore, vector: InMemoryVectorStore) -> bool:
    return (
        graph.consistency_capability() == "atomic"
        and vector.consistency_capability() == "atomic"
        and graph.engine_key() == vector.engine_key()
    )


def test_best_effort_rerun_after_compensation_is_idempotent(tmp_path: Path) -> None:
    """A-2 (4.3, L2-06): повтор джобы после компенсации — no-op, версия не растёт."""
    graph, vector = InMemoryGraphStore(), FailingVectorAxis()
    analyzer, registry = build_analyzer(tmp_path, graph, vector)
    src = tmp_path / "d.txt"
    src.write_text(CONTENT_1, encoding="utf-8")
    source_id = _source_node_id("it", "src://d.txt")

    with pytest.raises(CommitStageError):
        run_source(analyzer, src)
    version_after_failure = registry.latest_active("it", "src://d.txt")
    assert version_after_failure is not None and version_after_failure["version"] == 1

    # повтор без изменения контента — no-op: registry знает версию, _write не вызывается
    ctx = run_source(analyzer, src)
    assert ctx.commit_applied is True
    version_after_rerun = registry.latest_active("it", "src://d.txt")
    assert version_after_rerun is not None and version_after_rerun["version"] == 1
    assert graph.list_chunk_ids_of_source(source_id) == []


def test_best_effort_reindex_compensates_stale_vectors(tmp_path: Path) -> None:
    """A-2 (4.1 + L2-03): re-index — первый прогон успешен, второй падает на векторах.

    Компенсация должна удалить чанки ИЗ ГРАФА И ИЗ ВЕКТОРА (старый stale-vector
    не откатывается транзакцией, если delete_vectors ещё не записался — BUG #1 ревьюера).
    """
    vector = FailOnSecondUpsertVector()
    graph = InMemoryGraphStore()
    analyzer, registry = build_analyzer(tmp_path, graph, vector)
    src = tmp_path / "d.txt"
    source_id = _source_node_id("it", "src://d.txt")

    # --- первый прогон: CONTENT_1, обе оси записаны ---
    src.write_text(CONTENT_1, encoding="utf-8")
    run_source(analyzer, src)
    v1 = registry.latest_active("it", "src://d.txt")
    assert v1 is not None and v1["version"] == 1
    chunk_ids_v1 = graph.list_chunk_ids_of_source(source_id)
    assert len(chunk_ids_v1) > 0, "чанки записаны в граф"
    assert len(vector._vectors) > 0, "эмбеддинги записаны в вектор"

    # --- второй прогон: CONTENT_2, re-index → вектор падает → компенсация обеих осей ---
    src.write_text(CONTENT_2, encoding="utf-8")
    with pytest.raises(CommitStageError) as excinfo:
        run_source(analyzer, src)
    assert excinfo.value.compensated is True

    # граф пуст (чанки удалены)
    assert graph.list_chunk_ids_of_source(source_id) == []
    # ВЕКТОР тОЖЕ пуст: орфанов нет (L2-03)
    assert not vector._vectors, "старые эмбеддинги удалены при компенсации"
    # источник и сущность сохранились (историчность, ADR-014)
    assert graph.get_node(source_id) is not None
    assert graph.get_node(_entity_node_id("it", "дедупликация")) is not None
    v2 = registry.latest_active("it", "src://d.txt")
    assert v2 is not None and v2["version"] == 2

    # --- третий прогон: CONTENT_2 → no-op (L2-06) ---
    run_source(analyzer, src)
    assert registry.latest_active("it", "src://d.txt")["version"] == 2
    assert graph.list_chunk_ids_of_source(source_id) == []
    assert not vector._vectors


def test_atomic_pair_writes_both_axes_in_one_batch(tmp_path: Path) -> None:
    """A-2 (4.2/2.2): атомарная пара — обе оси через единый batch движка
    (Neo4j-контракт `atomic_batch`), без компенсации."""
    vector_axis = InMemoryVectorStore()
    graph = _AtomicBatchGraph(vector_axis)
    assert graph.consistency_capability() == vector_axis.consistency_capability() == "atomic"
    assert graph.engine_key() == vector_axis.engine_key()

    analyzer, _ = build_analyzer(tmp_path, graph, vector_axis)
    src = tmp_path / "d.txt"
    src.write_text(CONTENT_1, encoding="utf-8")
    run_source(analyzer, src)

    source_id = _source_node_id("it", "src://d.txt")
    chunk_ids = graph.list_chunk_ids_of_source(source_id)
    assert chunk_ids, "обе оси записаны через единый batch"
    # в единой транзакции лежат операции ОБЕИХ осей (граф: узлы+рёбра; вектор: delete+upsert)
    kinds = {kind for kind, _ in graph.batch_calls}
    assert {"upsert_nodes", "upsert_edges", "upsert_vectors"} <= kinds
    assert set(vector_axis._vectors) == set(chunk_ids), "оси согласованы после атомарного COMMIT"
    # компенсация не вызывалась: batch — единственный путь записи
    assert graph.delete_node_outside_tx == 0


class _AtomicBatchGraph(InMemoryGraphStore):
    """Граф-координатор атомарной пары: единая запись обеих осей (контракт A-2).

    Имитирует Neo4j-pair: `atomic_batch()` возвращает общий контекст, через который
    идут операции и графовой, и векторной осей (в прототипе — InMemory-делегирование).
    """

    def __init__(self, vector_axis: InMemoryVectorStore) -> None:
        super().__init__()
        self._vector_axis = vector_axis
        self.batch_calls: list[tuple[str, Any]] = []
        self.delete_node_outside_tx = 0

    @contextmanager
    def atomic_batch(self) -> Iterator[_RecordingBatch]:
        # упрощённая модель: без транзакционного отката внутри батча (в реальном Neo4j
        # его делает session/begin_transaction); исключение просто пробрасывается
        yield _RecordingBatch(self, self._vector_axis)

    def delete_node(self, node_id: str) -> bool:
        if self._journal is None:
            self.delete_node_outside_tx += 1
        return super().delete_node(node_id)


class _RecordingBatch:
    """Единый контекст обеих осей (A-2): логирует и делегирует операции в оси."""

    def __init__(self, graph: InMemoryGraphStore, vector: InMemoryVectorStore) -> None:
        self._graph = graph
        self._vector = vector

    def _record(self, kind: str, arg: Any) -> None:
        self._graph.batch_calls.append((kind, arg))

    def upsert_nodes(self, nodes: list[dict[str, Any]]) -> None:
        self._record("upsert_nodes", nodes)
        self._graph.upsert_nodes(nodes)

    def upsert_edges(self, edges: list[dict[str, Any]]) -> None:
        self._record("upsert_edges", edges)
        self._graph.upsert_edges(edges)

    def delete_node(self, node_id: str) -> bool:
        self._record("delete_node", node_id)
        return self._graph.delete_node(node_id)

    def upsert_vectors(self, items: list[dict[str, Any]]) -> None:
        self._record("upsert_vectors", items)
        self._vector.upsert_vectors(items)

    def delete_vectors(self, chunk_ids: list[str]) -> None:
        self._record("delete_vectors", chunk_ids)
        self._vector.delete_vectors(chunk_ids)