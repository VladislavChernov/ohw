"""Оркестратор 9 этапов Ingestion Pipeline (docs/02 §1).

Каждый этап — класс с методом run(ctx, document) -> ctx. Оркестратор прогоняет
их последовательно, при ошибке помечает джобу failed и останавливается.

Etap'ы работают ТОЛЬКО с каноническим Document (ADR-021) — без доступа к источнику.
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from graphrag_proto.retrieval.adapters.base import GraphStoreProvider, VectorStoreProvider
from graphrag_proto.retrieval.adapters.deterministic import deterministic_embedding

CHUNK_SIZE = 512
CHUNK_OVERLAP = 64
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
    """CHUNK: скользящее окно 512/64; code-блоки не рвутся (нарезка по блокам)."""

    name = "CHUNK"

    def run(self, ctx: PipelineContext) -> None:
        chunks: list[str] = []
        for block in ctx.document.blocks:
            if block.type not in ("text", "code"):
                continue
            if not isinstance(block.data, str) or not block.data.strip():
                continue
            chunks += _sliding_window(block.data, CHUNK_SIZE, CHUNK_OVERLAP)
        ctx.chunks = chunks
        ctx.chunks_meta = [{"index": i, "size_tokens": len(c)} for i, c in enumerate(chunks)]


def _sliding_window(text: str, size: int, overlap: int) -> list[str]:
    """Нарезчик по словам (stub: M1 — без токенизатора/модели сегментации).

    Оперирует в пределах одного блока (text|code), поэтому фрагменты кода,
    попавшие в code-блок, сохраняются целиком и не разрываются пополам.
    """
    if len(text) <= size:
        return [text] if text.strip() else []
    words = text.split(" ")
    chunks: list[str] = []
    step = max(size - overlap, 1)
    for i in range(0, max(len(words), 1), step):
        chunk = " ".join(words[i : i + size])
        if chunk.strip():
            chunks.append(chunk)
    return chunks


class EmbedStage(Stage):
    """EMBED: детерминированный фейк (общий с ретривером — L2-04 согласованность оси);
    боевые bge-m3 — M3."""

    name = "EMBED"

    def run(self, ctx: PipelineContext) -> None:
        for meta in ctx.chunks_meta:
            chunk = ctx.chunks[meta["index"]]
            meta["embedding"] = deterministic_embedding(chunk)
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
        import urllib.request

        for entity in ctx.entities:
            body = json.dumps(
                {"term": entity["name"], "domain": ctx.domain}
            ).encode("utf-8")
            req = urllib.request.Request(
                f"{self._glossary_url}/api/v1/glossary/resolve",
                data=body,
                headers={"Content-Type": "application/json"},
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


class CommitStage(Stage):
    """COMMIT: атомарная запись узлов/рёбер + эмбеддингов чанков (L2-04).

    M2 (ADR-023/design D5): документ регистрируется в DocumentRegistry (SQLite,
    источник версий/идемпотентности — ADR-014), а узлы/рёбра/векторы пишутся через
    GraphStoreProvider/VectorStoreProvider в одной транзакции (rollback при ошибке).
    Повторная загрузка неизменённого контента — no-op (новая версия не пишется).
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
        if self._graph_store is None or self._vector_store is None:
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

        stale_chunks = self._graph_store.list_chunk_ids_of_source(source_id)

        with self._graph_store.transaction() as graph_tx, self._vector_store.transaction() as vector_tx:
            for chunk_id in stale_chunks:
                graph_tx.delete_node(chunk_id)
            vector_tx.delete_vectors(stale_chunks)
            graph_tx.upsert_nodes(nodes)
            graph_tx.upsert_edges(edges)
            vector_tx.upsert_vectors(vectors)


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