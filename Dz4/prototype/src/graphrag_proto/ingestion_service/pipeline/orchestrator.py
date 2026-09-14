"""Оркестратор 9 этапов Ingestion Pipeline (docs/02 §1).

Каждый этап — класс с методом run(ctx, document) -> ctx. Оркестратор прогоняет
их последовательно, при ошибке помечает джобу failed и останавливается.

Etap'ы работают ТОЛЬКО с каноническим Document (ADR-021) — без доступа к источнику.
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from graphrag_proto.ingestion_service.pipeline.chunker import Chunker, build_chunker_for
from graphrag_proto.retrieval.adapters.base import Embedder, GraphStoreProvider, VectorStoreProvider
from graphrag_proto.retrieval.adapters.deterministic import DeterministicEmbedder

DEDUP_AUTO = 0.92
DEDUP_LLM = 0.75
SIMILAR_TO = 0.85

EXTRACTOR_VERSION = "deterministic:v1"

SOURCE_LABEL = "Source"
CHUNK_LABEL = "Chunk"
ENTITY_LABEL = "Entity"


def _chunk_id(source_url: str, index: int) -> str:
    """Стабильный идентификатор чанка (L2-04: оси связаны по chunk_id)."""
    digest = hashlib.sha256(f"{source_url}:{index}".encode()).hexdigest()[:12]
    return f"chk:{digest}"


def _source_node_id(domain: str, source_url: str) -> str:
    return f"src:{domain}:{source_url}"


def _entity_node_id(domain: str, canonical_name: str) -> str:
    return f"ent:{domain}:{canonical_name}"

STAGES = (
    "INGEST",
    "CHUNK",
    "EMBED",
    "EXTRACT",
    "NORMALIZE",
    "DEDUP",
    "CONTRACT",
    "VALIDATE",
    "COMMIT",
)


@dataclass
class PipelineContext:
    job_id: str
    domain: str
    doc_type: str
    source_url: str
    source_path: str | None = None
    document: Any = None
    chunks: list[str] = field(default_factory=list)
    chunks_meta: list[dict[str, Any]] = field(default_factory=list)
    entities: list[dict[str, Any]] = field(default_factory=list)
    registry_result: tuple[str, int, bool] | None = None
    commit_applied: bool = False


class Stage(ABC):
    name: str

    @abstractmethod
    def run(self, ctx: PipelineContext) -> None:
        raise NotImplementedError


class IngestStage(Stage):
    """INGEST: вызов DocumentReader (walk source_path)."""

    name = "INGEST"

    def __init__(self, readers: dict[str, Any]) -> None:
        self._readers = readers

    def run(self, ctx: PipelineContext) -> None:
        reader = self._readers[ctx.doc_type]
        from pathlib import Path

        document = reader.read(Path(ctx.source_path or ""), ctx.source_url, ctx.domain)
        ctx.document = document


class ChunkStage(Stage):
    """CHUNK: фрагментация блоков через `Chunker`.

    `chunker` — фиксированный чанкер (DI в тестах); при `None` чанкер резолвится
    per-job через `build_chunker_for(ctx.domain)` — precedence env > профиль домена
    > namespaces > дефолты (512/64). code-блоки не рвутся: чанкер оперирует
    в пределах одного блока.
    """

    name = "CHUNK"

    def __init__(self, chunker: Chunker | None = None) -> None:
        if chunker is not None:
            self._chunkers: Callable[[str], Chunker] = lambda _domain: chunker
        else:
            self._chunkers = build_chunker_for

    def run(self, ctx: PipelineContext) -> None:
        chunker = self._chunkers(ctx.domain)
        chunks: list[str] = []
        for block in ctx.document.blocks:
            if block.type not in ("text", "code"):
                continue
            if not isinstance(block.data, str) or not block.data.strip():
                continue
            chunks += chunker.chunk(block.data)
        ctx.chunks = chunks
        ctx.chunks_meta = [{"index": i, "size_tokens": len(c)} for i, c in enumerate(chunks)]


class EmbedStage(Stage):
    """EMBED: эмбеддинг чанков (адаптер оси; L2-04 — общий с ретривером).

    По умолчанию `DeterministicEmbedder(dim=8)` (M2-совместимость и тесты);
    bge_m3_service — M3, связь ingest/query через один провайдер.
    """

    name = "EMBED"

    def __init__(self, embedder: Embedder | None = None) -> None:
        self._embedder = embedder or DeterministicEmbedder()

    def run(self, ctx: PipelineContext) -> None:
        for meta in ctx.chunks_meta:
            chunk = ctx.chunks[meta["index"]]
            meta["embedding"] = self._embedder.embed(chunk, ctx.domain)
            meta["chunk_id"] = _chunk_id(ctx.source_url, meta["index"])


class ExtractStage(Stage):
    """EXTRACT: заглушка (детерминированные сущности); боевой LLM — M3."""

    name = "EXTRACT"

    def run(self, ctx: PipelineContext) -> None:
        seen: set[str] = set()
        for chunk in ctx.chunks:
            for token in chunk.split():
                word = "".join(c for c in token.lower() if c.isalpha())
                if len(word) >= 5 and word.isalpha() and word not in seen:
                    seen.add(word)
                    ctx.entities.append(
                        {"name": word, "source": ctx.source_url, "canonical": word}
                    )


class NormalizeStage(Stage):
    """NORMALIZE: канонизация через Glossary HTTP (resolve)."""

    name = "NORMALIZE"

    def __init__(self, glossary_url: str) -> None:
        self._glossary_url = glossary_url.rstrip("/")

    def run(self, ctx: PipelineContext) -> None:
        if not self._glossary_url:
            return
        import os
        import urllib.request

        headers = {"Content-Type": "application/json"}
        api_key = os.environ.get("AUTH_API_KEY") or os.environ.get("GRAPH_AUTH_API_KEY", "")
        if api_key:
            headers["X-API-Key"] = api_key

        for entity in ctx.entities:
            body = json.dumps(
                {"term": entity["name"], "domain": ctx.domain}
            ).encode("utf-8")
            req = urllib.request.Request(
                f"{self._glossary_url}/api/v1/glossary/resolve",
                data=body,
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=2) as resp:
                    payload = json.loads(resp.read().decode("utf-8"))
                canonical = payload.get("canonical_name") or entity["name"]
            except Exception:  # noqa: BLE001 - glossary недоступен -> оставляем имя как есть
                canonical = entity["name"]
            entity["canonical"] = canonical


class DedupStage(Stage):
    """DEDUP: двухступенчатая дедупликация (M1 — детерминированная заглушка)."""

    name = "DEDUP"

    def run(self, ctx: PipelineContext) -> None:
        canonical_map: dict[str, dict[str, Any]] = {}
        for entity in ctx.entities:
            key = entity["canonical"]
            if key not in canonical_map:
                canonical_map[key] = {
                    "name": key,
                    "sources": [ctx.source_url],
                    "variants": [entity["name"]],
                }
            else:
                canonical_map[key]["sources"].append(ctx.source_url)
        ctx.entities = list(canonical_map.values())


class ContractStage(Stage):
    """CONTRACT: склейка иерархии (M1 — заглушка, сохраняем контрактные узлы)."""

    name = "CONTRACT"

    def run(self, ctx: PipelineContext) -> None:
        for entity in ctx.entities:
            entity["contract"] = True


class ValidateStage(Stage):
    """VALIDATE: структурная валидация сущностей (M1 — базовая)."""

    name = "VALIDATE"

    def run(self, ctx: PipelineContext) -> None:
        for entity in ctx.entities:
            if not entity.get("name"):
                raise ValueError("сущность без имени на VALIDATE")
            if not entity.get("sources"):
                raise ValueError("сущность без источников на VALIDATE")


class CommitStageError(RuntimeError):
    """Сбой COMMIT (A-2, ADR-024). `compensated=True` — best_effort-пара: сбой второй
    оси, граф откачен компенсацией; джоба завершается failed с пометкой «компенсировано»."""

    def __init__(self, message: str, *, compensated: bool) -> None:
        super().__init__(message)
        self.compensated = compensated


def _is_atomic_pair(graph: GraphStoreProvider, vector: VectorStoreProvider) -> bool:
    """Атомарная пара (A-2): обе оси декларируют `"atomic"` И обслуживаются общим
    движком (`engine_key()` совпадают). Иначе — best_effort."""
    return (
        graph.consistency_capability() == "atomic"
        and vector.consistency_capability() == "atomic"
        and graph.engine_key() is not None
        and graph.engine_key() == vector.engine_key()
    )


def _apply_axes(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    vectors: list[dict[str, Any]],
    stale_chunks: list[str],
    graph_tx: Any,
    vector_tx: Any,
) -> None:
    """Применение плана записи к контекстам осей (в атомарном пути `graph_tx` и
    `vector_tx` — один объект batch движка)."""
    for chunk_id in stale_chunks:
        graph_tx.delete_node(chunk_id)
    vector_tx.delete_vectors(stale_chunks)
    graph_tx.upsert_nodes(nodes)
    graph_tx.upsert_edges(edges)
    vector_tx.upsert_vectors(vectors)


class CommitStage(Stage):
    """COMMIT: атомарная запись узлов/рёбер + эмбеддингов чанков (L2-04, A-2).

    M2 (ADR-023/design D5): документ регистрируется в DocumentRegistry (SQLite,
    источник версий/идемпотентности — ADR-014), а узлы/рёбра/векторы пишутся через
    GraphStoreProvider/VectorStoreProvider. Повторная загрузка неизменённого контента —
    no-op (новая версия не пишется).

    A-2 (ADR-024): стратегия по capability пары. Атомарная пара (оба `"atomic"` + общий
    `engine_key()`) пишет обе оси в одной транзакции движка; иначе — best_effort:
    последовательный commit графа → вектора, при сбое второй оси граф компенсируется
    удалением записанных чанков, джоба failed с пометкой «компенсировано».
    """

    name = "COMMIT"

    def __init__(
        self,
        registry: Any,
        graph_store: GraphStoreProvider | None = None,
        vector_store: VectorStoreProvider | None = None,
    ) -> None:
        self._registry = registry
        self._graph_store = graph_store
        self._vector_store = vector_store

    def run(self, ctx: PipelineContext) -> None:
        doc = ctx.document
        result = self._registry.upsert(doc)
        ctx.registry_result = result
        ctx.commit_applied = True
        _doc_id, _version, created_new = result
        if not created_new:
            return  # idempotent no-op (L2-06): контент не изменился
        self._write(doc, ctx)

    def _write(self, doc: Any, ctx: PipelineContext) -> None:
        graph = self._graph_store
        vector = self._vector_store
        if graph is None or vector is None:
            return
        domain = doc.domain
        source_url = doc.source_url
        source_id = _source_node_id(domain, source_url)

        nodes: list[dict[str, Any]] = [
            {
                "node_id": source_id,
                "labels": [SOURCE_LABEL],
                "properties": {
                    "source_url": source_url,
                    "domain": domain,
                    "doc_type": doc.doc_type,
                },
            }
        ]
        for entity in ctx.entities:
            canonical = str(entity.get("canonical") or entity.get("name") or "")
            nodes.append(
                {
                    "node_id": _entity_node_id(domain, canonical),
                    "labels": [ENTITY_LABEL],
                    "properties": {
                        "canonical_name": canonical,
                        "source_ids": list(entity.get("sources") or []),
                        "extractor_version": EXTRACTOR_VERSION,
                        "variants": list(entity.get("variants") or []),
                    },
                }
            )

        edges: list[dict[str, Any]] = []
        vectors: list[dict[str, Any]] = []
        for meta in ctx.chunks_meta:
            chunk_id = meta["chunk_id"]
            text = ctx.chunks[meta["index"]]
            nodes.append(
                {
                    "node_id": chunk_id,
                    "labels": [CHUNK_LABEL],
                    "properties": {
                        "chunk_id": chunk_id,
                        "text": text,
                        "source_url": source_url,
                        "domain": domain,
                        "index": meta["index"],
                    },
                }
            )
            edges.append(
                {"from_id": source_id, "to_id": chunk_id, "type": "CONTAINS", "properties": {}}
            )
            vectors.append(
                {
                    "chunk_id": chunk_id,
                    "embedding": meta["embedding"],
                    "metadata": {
                        "text": text,
                        "source_url": source_url,
                        "domain": domain,
                        "index": meta["index"],
                    },
                }
            )

        stale_chunks = graph.list_chunk_ids_of_source(source_id)
        written_chunk_ids = [meta["chunk_id"] for meta in ctx.chunks_meta]

        if _is_atomic_pair(graph, vector):
            self._write_atomic(graph, vector, nodes, edges, vectors, stale_chunks)
            return
        self._write_best_effort(graph, vector, nodes, edges, vectors, stale_chunks, written_chunk_ids)

    def _write_atomic(
        self,
        graph: GraphStoreProvider,
        vector: VectorStoreProvider,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        vectors: list[dict[str, Any]],
        stale_chunks: list[str],
    ) -> None:
        """Атомарная пара: обе оси в одной транзакции движка (A-2).

        При наличии `atomic_batch()` (Neo4j-пара — один `session.begin_transaction()`)
        — запись через batch; иначе (InMemory-пара, единый процесс) — вложенные
        `transaction()`-контексты: исключение откатывает обе оси.
        """
        batch = graph.atomic_batch()
        if batch is not None:
            with batch as tx:
                _apply_axes(nodes, edges, vectors, stale_chunks, tx, tx)
            return
        with graph.transaction() as graph_tx, vector.transaction() as vector_tx:
            _apply_axes(nodes, edges, vectors, stale_chunks, graph_tx, vector_tx)

    def _write_best_effort(
        self,
        graph: GraphStoreProvider,
        vector: VectorStoreProvider,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        vectors: list[dict[str, Any]],
        stale_chunks: list[str],
        written_chunk_ids: list[str],
    ) -> None:
        """best_effort-пара (A-2): commit графа, затем вектора.

        Сбой первой оси — джоба failed без компенсации (ничего не записано). Сбой второй
        оси — граф компенсируется: удаляются записанные чанки (`delete_node`), остаются
        Source/Entity; ошибка пробрасывается как `CommitStageError(compensated=True)`.
        Повтор той же джобы идемпотентен (L2-06): registry знает версию, контент
        не изменился → write не выполняется.
        """
        with graph.transaction() as graph_tx:
            for chunk_id in stale_chunks:
                graph_tx.delete_node(chunk_id)
            graph_tx.upsert_nodes(nodes)
            graph_tx.upsert_edges(edges)
        try:
            with vector.transaction() as vector_tx:
                vector_tx.delete_vectors(stale_chunks)
                vector_tx.upsert_vectors(vectors)
        except Exception as exc:
            # компенсация обеих осей по полному следу джобы (stale + новые): граф уже
            # коммичен, а векторная транзакция откатила и delete устаревших чанков,
            # поэтому удалять надо и stale (старые эмбеддинги остались в векторе),
            # и written (L2-03: без орфанов ни в одной из осей)
            compensate_ids = sorted(set(stale_chunks) | set(written_chunk_ids))
            for chunk_id in compensate_ids:
                graph.delete_node(chunk_id)
            vector.delete_vectors(compensate_ids)
            raise CommitStageError(
                "COMMIT best_effort: сбой второй оси, граф компенсирован "
                f"(удалено чанков: {len(compensate_ids)})",
                compensated=True,
            ) from exc


def soft_delete_source(
    registry: Any,
    graph_store: GraphStoreProvider,
    vector_store: VectorStoreProvider,
    domain: str,
    source_url: str,
) -> bool:
    """Soft-delete источника (L2-05): чанки снимаются с поиска, сущности остаются.

    Возвращает True, если источник имел активную версию и был помечен deleted.
    """
    if not registry.soft_delete(domain, source_url):
        return False
    source_id = _source_node_id(domain, source_url)
    chunk_ids = graph_store.list_chunk_ids_of_source(source_id)
    with graph_store.transaction() as graph_tx, vector_store.transaction() as vector_tx:
        for chunk_id in chunk_ids:
            graph_tx.delete_node(chunk_id)
        vector_tx.delete_vectors(chunk_ids)
    return True


class Analyzer:
    """Оркестратор последовательного прогона этапов."""

    def __init__(self, stages: list[Stage]) -> None:
        self._stages = {s.name: s for s in stages}

    def run(self, ctx: PipelineContext) -> None:
        for name in STAGES:
            self._stages[name].run(ctx)

    def run_one(self, name: str, ctx: PipelineContext) -> None:
        self._stages[name].run(ctx)