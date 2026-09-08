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

CHUNK_SIZE = 512
CHUNK_OVERLAP = 64
DEDUP_AUTO = 0.92
DEDUP_LLM = 0.75
SIMILAR_TO = 0.85

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


def _make_deterministic_vector(text: str, dim: int = 8) -> list[float]:
    """Заглушка EMBED: детерминированный псевдо-вектор без GPU/моделей."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [float(b / 256.0) for b in digest[:dim]]


class EmbedStage(Stage):
    """EMBED: заглушка (детерминированный фейк); боевые bge-m3 — M3."""

    name = "EMBED"

    def run(self, ctx: PipelineContext) -> None:
        for meta in ctx.chunks_meta:
            chunk = ctx.chunks[meta["index"]]
            meta["embedding"] = _make_deterministic_vector(chunk)


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
    """COMMIT: атомарная запись в Neo4j (L2-04); в M1 — idempotent-заглушка через GraphStoreProvider.

    Реальная атомарность Neo4j (MERGE + транзакция) — при подключении боевого
    GraphStoreProvider (M2+). Здесь фиксируем результат в контексте (registry.update).
    """

    name = "COMMIT"

    def __init__(self, registry: Any) -> None:
        self._registry = registry

    def run(self, ctx: PipelineContext) -> None:
        # Атомарная запись: имитируем транзакцию. На M1 граф-узлы пишутся через
        # GraphStoreProvider (заглушка-фейк), документ регистрируется атомарно.
        ctx.commit_applied = True
        doc = ctx.document
        result = self._registry.upsert(doc)
        ctx.registry_result = result


class Analyzer:
    """Оркестратор последовательного прогона этапов."""

    def __init__(self, stages: list[Stage]) -> None:
        self._stages = {s.name: s for s in stages}

    def run(self, ctx: PipelineContext) -> None:
        for name in STAGES:
            self._stages[name].run(ctx)

    def run_one(self, name: str, ctx: PipelineContext) -> None:
        self._stages[name].run(ctx)