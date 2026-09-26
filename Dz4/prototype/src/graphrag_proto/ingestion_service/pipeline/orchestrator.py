"""Оркестратор 9 этапов Ingestion Pipeline (docs/02 §1).

Каждый этап — класс с методом run(ctx, document) -> ctx. Оркестратор прогоняет
их последовательно, при ошибке помечает джобу failed и останавливается.

Etap'ы работают ТОЛЬКО с каноническим Document (ADR-021) — без доступа к источнику.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import sys
import time
import unicodedata
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from difflib import SequenceMatcher
from typing import Any, TypeVar

from graphrag_proto.ingestion_service.pipeline.chunker import (
    Chunker,
    build_chunker,
    build_chunker_for,
    build_chunker_from_profile,
)
from graphrag_proto.ingestion_service.projection import (
    ProjectionState,
    ProjectionStateStore,
    projection_revision_for,
    projection_transition_allowed,
)
from graphrag_proto.retrieval.adapters.base import Embedder, GraphStoreProvider, VectorStoreProvider
from graphrag_proto.retrieval.adapters.deterministic import DeterministicEmbedder

EXTRACTOR_VERSION = "deterministic:v1"

SOURCE_LABEL = "Source"
CHUNK_LABEL = "Chunk"
ENTITY_LABEL = "Entity"
CONTEXT_NODE_LABEL = "ContextNode"

ProfileFetcher = Callable[[str], dict[str, Any]]


def _llm_extraction_enabled() -> bool:
    return os.environ.get("EXTRACT_LLM", "").strip().lower() in {"1", "true", "yes", "on"}


def _load_profile(ctx: PipelineContext, profile_fetcher: ProfileFetcher | None) -> None:
    if ctx.profile_loaded:
        return
    if ctx.profile is not None:
        ctx.profile_loaded = True
        return
    if profile_fetcher is None:
        ctx.profile = {}
        ctx.profile_loaded = True
        return
    try:
        profile = profile_fetcher(ctx.domain)
    except Exception as exc:  # noqa: BLE001
        ctx.profile = {}
        ctx.profile_loaded = True
        ctx.profile_error = str(exc)
        return
    if not isinstance(profile, dict):
        ctx.profile = {}
        ctx.profile_loaded = True
        ctx.profile_error = f"профиль домена {ctx.domain!r} должен быть mapping"
        return
    ctx.profile = profile
    ctx.profile_loaded = True


def _profile_llm_enabled(profile: dict[str, Any]) -> bool:
    extraction = profile.get("extraction")
    if isinstance(extraction, dict) and isinstance(extraction.get("llm_enabled"), bool):
        return bool(extraction["llm_enabled"])
    template = extraction.get("prompt_template") if isinstance(extraction, dict) else None
    return isinstance(template, dict) and all(
        isinstance(template.get(key), str) and bool(template[key]) for key in ("system", "user")
    )


def _identity_key(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value))
    normalized = normalized.replace("Ё", "Е").replace("ё", "е")
    return " ".join(normalized.casefold().split())


def _extraction_template(profile: dict[str, Any]) -> dict[str, Any]:
    extraction = profile.get("extraction")
    template = extraction.get("prompt_template") if isinstance(extraction, dict) else None
    if not isinstance(template, dict):
        raise TypeError("в профиле отсутствует extraction.prompt_template")
    if not isinstance(template.get("user"), str) or not isinstance(template.get("system"), str):
        raise TypeError("extraction.prompt_template должен содержать system и user")
    return template


def _parse_extraction_payload(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("EXTRACT LLM должен вернуть валидный JSON") from exc
    if not isinstance(payload, dict):
        raise TypeError("EXTRACT LLM должен вернуть JSON-объект")
    return payload


def _profile_node_types(profile: dict[str, Any] | None) -> list[dict[str, Any]]:
    if profile is None:
        return []
    ontology = profile.get("ontology")
    raw = ontology.get("node_types") if isinstance(ontology, dict) else None
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict) and isinstance(item.get("type"), str)]


def _plural_entity_key(entity_type: str) -> str:
    normalized = entity_type.casefold()
    if normalized.endswith(("s", "x", "z", "ch", "sh")):
        return f"{normalized}es"
    if normalized.endswith("y") and len(normalized) > 1:
        return f"{normalized[:-1]}ies"
    return f"{normalized}s"


def _profile_entity_payloads(profile: dict[str, Any]) -> dict[str, str]:
    return {
        _plural_entity_key(str(node_type["type"])): CONTEXT_NODE_LABEL
        for node_type in _profile_node_types(profile)
    }


def _entity_types(entity: dict[str, Any]) -> list[str]:
    raw = entity.get("types")
    if isinstance(raw, list):
        values = [str(value) for value in raw if isinstance(value, str) and value]
    else:
        value = entity.get("type")
        values = [str(value)] if isinstance(value, str) and value else []
    return list(dict.fromkeys(values or [CONTEXT_NODE_LABEL]))


def _entity_chunk_ids(entity: dict[str, Any]) -> list[str]:
    raw = entity.get("chunk_ids")
    if not isinstance(raw, list):
        return []
    return list(dict.fromkeys(str(value) for value in raw if str(value)))


def _entity_sources(entity: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("sources", "source_ids"):
        raw = entity.get(key)
        if isinstance(raw, list):
            values.extend(str(value) for value in raw if str(value))
        elif isinstance(raw, str) and raw:
            values.append(raw)
    value = entity.get("source")
    if isinstance(value, str) and value:
        values.append(value)
    return list(dict.fromkeys(values))


def _entity_variants(entity: dict[str, Any]) -> list[str]:
    values: list[str] = []
    raw = entity.get("variants")
    if isinstance(raw, list):
        values.extend(str(value) for value in raw if str(value))
    for key in ("name", "canonical"):
        value = entity.get(key)
        if isinstance(value, str) and value:
            values.append(value)
    return list(dict.fromkeys(values))


def _origin_rank(value: object) -> int:
    return {"ai": 1, "system": 2, "user": 3}.get(str(value or ""), 0)


def _safe_entity_description(value: object, ctx: PipelineContext) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = _identity_key(value)
    if not normalized:
        return None
    chunk_keys = [_identity_key(chunk) for chunk in ctx.chunks]
    for chunk in chunk_keys:
        if not chunk:
            continue
        if normalized in chunk or chunk in normalized:
            return None
        if SequenceMatcher(None, normalized, chunk).find_longest_match().size >= 20:
            return None
    return value


def _entity_record(
    entity_type: str,
    item: dict[str, Any],
    ctx: PipelineContext,
    chunk_id: str,
    extractor_version: str,
) -> dict[str, Any]:
    canonical = item.get("canonical_name") or item.get("canonical") or item.get("name")
    if not isinstance(canonical, str) or not canonical.strip():
        raise ValueError(f"сущность {entity_type} должна иметь имя")
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        name = canonical
    record: dict[str, Any] = {
        "type": entity_type,
        "name": name,
        "canonical": canonical,
        "source": ctx.source_url,
        "sources": [ctx.source_url],
        "chunk_ids": [chunk_id],
        "extractor_version": extractor_version,
        "origin": "ai",
        "variants": [name],
    }
    for key in ("id", "description", "category", "tag_id", "confidence"):
        value = item.get(key)
        if key == "description":
            value = _safe_entity_description(value, ctx)
        if value is not None:
            record[key] = value
    record["origin"] = "ai"
    properties = item.get("properties")
    if isinstance(properties, dict):
        record["properties"] = dict(properties)
    aliases = item.get("aliases")
    if isinstance(aliases, list):
        record["aliases"] = [str(value) for value in aliases if str(value)]
    return record


def _chunk_id(domain: str, source_url: str, index: int) -> str:
    """Стабильный идентификатор чанка (L2-04: оси связаны по chunk_id)."""
    digest = hashlib.sha256(f"{domain}:{source_url}:{index}".encode()).hexdigest()[:12]
    return f"chk:{digest}"


def _source_node_id(domain: str, source_url: str) -> str:
    return f"src:{domain}:{source_url}"


def _entity_node_id(domain: str, canonical_name: str) -> str:
    return f"ent:{domain}:{_identity_key(canonical_name)}"


def _context_node_id(domain: str, entity: dict[str, Any]) -> str:
    raw = entity.get("tag_id") or entity.get("node_id")
    if isinstance(raw, str) and raw:
        prefix = f"tag:{domain}:"
        return raw if raw.startswith(prefix) else f"{prefix}{raw}"
    canonical = str(entity.get("canonical_name") or entity.get("canonical") or entity.get("name") or "")
    return f"tag:{domain}:{_identity_key(canonical)}"

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
    entity_edges: list[dict[str, Any]] = field(default_factory=list)
    tags: list[dict[str, Any]] = field(default_factory=list)
    links: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    profile: dict[str, Any] | None = None
    profile_loaded: bool = False
    profile_error: str | None = None
    enrichment_degraded: bool = False
    enrichment_error: str | None = None
    graph_projection_status: str = "not_requested"
    graph_projection_error: str | None = None
    registry_result: tuple[str, int, bool] | None = None
    commit_applied: bool = False
    noop: bool = False


class Stage(ABC):
    name: str

    @abstractmethod
    def run(self, ctx: PipelineContext) -> None:
        raise NotImplementedError

    def try_noop(self, ctx: PipelineContext) -> bool:
        return False


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

    def __init__(
        self,
        chunker: Chunker | None = None,
        profile_fetcher: ProfileFetcher | None = None,
    ) -> None:
        self._profile_fetcher = profile_fetcher
        self._chunker = chunker
        self._chunkers: Callable[[str], Chunker] = build_chunker_for

    def run(self, ctx: PipelineContext) -> None:
        _load_profile(ctx, self._profile_fetcher)
        if self._chunker is not None:
            chunker = self._chunker
        elif ctx.profile is not None:
            chunker = build_chunker_from_profile(ctx.profile)
        elif self._profile_fetcher is None:
            chunker = build_chunker()
        else:
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
            meta["chunk_id"] = _chunk_id(ctx.domain, ctx.source_url, meta["index"])


class ExtractStage(Stage):
    """EXTRACT: LLM или детерминированный fallback."""

    name = "EXTRACT"

    def __init__(
        self,
        llm: Any | None = None,
        profile_fetcher: ProfileFetcher | None = None,
        optional_failure: bool = False,
    ) -> None:
        self._llm = llm
        self._profile_fetcher = profile_fetcher
        self._optional_failure = optional_failure

    def run(self, ctx: PipelineContext) -> None:
        if not _llm_extraction_enabled():
            _load_profile(ctx, self._profile_fetcher)
            self._extract_deterministic(ctx)
            return
        if getattr(self._llm, "is_fake", False):
            if not ctx.profile_loaded:
                ctx.profile = {}
                ctx.profile_loaded = True
            self._extract_deterministic(ctx)
            return
        _load_profile(ctx, self._profile_fetcher)
        if ctx.profile_error:
            ctx.enrichment_degraded = True
            ctx.enrichment_error = ctx.profile_error
        if not ctx.profile or not _profile_llm_enabled(ctx.profile) or self._llm is None:
            self._extract_deterministic(ctx)
            return
        if not self._optional_failure:
            self._extract_llm(ctx)
            return
        try:
            self._extract_llm(ctx)
        except Exception as exc:  # noqa: BLE001
            ctx.entities = []
            ctx.entity_edges = []
            ctx.enrichment_degraded = True
            ctx.enrichment_error = str(exc)
            self._extract_deterministic(ctx)

    def _extract_llm(self, ctx: PipelineContext) -> None:
        template = _extraction_template(ctx.profile or {})
        prompt_id = str(template.get("id") or "extract")
        extractor_version = f"llm:{prompt_id}"
        entity_payloads = {
            "tags": CONTEXT_NODE_LABEL,
            "entities": CONTEXT_NODE_LABEL,
        }
        entity_payloads.update(
            {
                key: CONTEXT_NODE_LABEL
                for key in _profile_entity_payloads(ctx.profile or {})
            }
        )
        allowed_fields = {"relationships", "links", *entity_payloads}
        llm = self._llm
        if llm is None:
            raise RuntimeError("EXTRACT: LLM adapter is required for profile extraction")
        for index, chunk in enumerate(ctx.chunks):
            chunk_id = self._chunk_id(ctx, index)
            prompt = (
                f"{template['user']}\n\n"
                f"Текст документа для извлечения (чанк {index + 1}):\n{chunk}"
            )
            raw = "".join(
                llm.generate(prompt, system=str(template["system"]), stream=False)
            )
            payload = _parse_extraction_payload(raw)
            records: list[dict[str, Any]] = []
            unknown_fields = set(payload) - allowed_fields
            if unknown_fields:
                raise ValueError(f"EXTRACT содержит неизвестные поля: {sorted(unknown_fields)}")
            for key, entity_type in entity_payloads.items():
                items = payload.get(key, [])
                if not isinstance(items, list):
                    raise TypeError(f"EXTRACT поле {key!r} должно быть списком")
                for item in items:
                    if not isinstance(item, dict):
                        raise TypeError(f"EXTRACT поле {key!r} содержит не-объект")
                    records.append(
                        _entity_record(
                            entity_type,
                            item,
                            ctx,
                            chunk_id,
                            extractor_version,
                        )
                    )
            relations = payload.get("relationships", payload.get("links", []))
            self._validate_edges(relations, records)
            ctx.entities.extend(records)
            for relation in relations:
                edge: dict[str, Any] = {}
                if relation.get("from") is not None:
                    edge["from"] = str(relation["from"])
                if relation.get("from_id") is not None:
                    edge["from_id"] = str(relation["from_id"])
                if relation.get("to") is not None:
                    edge["to"] = str(relation["to"])
                if relation.get("to_id") is not None:
                    edge["to_id"] = str(relation["to_id"])
                edge["kind"] = str(relation.get("kind") or relation.get("type") or "RELATED")
                for key in (
                    "confidence",
                    "source_ids",
                    "properties",
                ):
                    if key in relation:
                        edge[key] = relation[key]
                edge["origin"] = "ai"
                ctx.entity_edges.append(edge)

    def _chunk_id(self, ctx: PipelineContext, index: int) -> str:
        for meta in ctx.chunks_meta:
            if meta.get("index") == index:
                return str(meta["chunk_id"])
        return _chunk_id(ctx.domain, ctx.source_url, index)

    @staticmethod
    def _validate_edges(
        relations: object,
        records: list[dict[str, Any]],
    ) -> None:
        if not isinstance(relations, list):
            raise TypeError("EXTRACT поле relationships должно быть списком")
        known: set[str] = set()
        for record in records:
            for key in ("tag_id", "canonical", "canonical_name", "name"):
                value = record.get(key)
                if isinstance(value, str) and value:
                    known.add(_identity_key(value))
        for relation in relations:
            if not isinstance(relation, dict):
                raise TypeError("EXTRACT relationship должен быть объектом")
            source = relation.get("from") or relation.get("from_id")
            target = relation.get("to") or relation.get("to_id")
            if not isinstance(source, str) or not source or not isinstance(target, str) or not target:
                raise ValueError("relationship должен содержать from и to")
            if _identity_key(source) not in known or _identity_key(target) not in known:
                raise ValueError("relationship ссылается на неизвестную сущность")

    def _extract_deterministic(self, ctx: PipelineContext) -> None:
        seen: dict[str, dict[str, Any]] = {}
        for index, chunk in enumerate(ctx.chunks):
            chunk_id = self._chunk_id(ctx, index)
            for token in chunk.split():
                word = "".join(c for c in token.lower() if c.isalpha())
                if len(word) < 5 or not word.isalpha():
                    continue
                current = seen.get(word)
                if current is None:
                    current = {
                        "type": CONTEXT_NODE_LABEL,
                        "name": word,
                        "canonical_name": word,
                        "tag_id": f"tag:{ctx.domain}:{word}",
                        "source": ctx.source_url,
                        "sources": [ctx.source_url],
                        "canonical": word,
                        "chunk_ids": [chunk_id],
                        "extractor_version": EXTRACTOR_VERSION,
                        "origin": "system",
                        "variants": [word],
                    }
                    seen[word] = current
                    ctx.entities.append(current)
                    continue
                current["chunk_ids"] = list(dict.fromkeys([*_entity_chunk_ids(current), chunk_id]))
                current["sources"] = list(dict.fromkeys([*_entity_sources(current), ctx.source_url]))
                current["variants"] = list(dict.fromkeys([*_entity_variants(current), word]))


class NormalizeStage(Stage):
    """NORMALIZE: канонизация через Glossary HTTP (resolve)."""

    name = "NORMALIZE"

    def __init__(self, glossary_url: str) -> None:
        self._glossary_url = glossary_url.rstrip("/")

    def run(self, ctx: PipelineContext) -> None:
        mapping: dict[str, str] = {}
        canonical_ids: dict[str, str] = {}
        ambiguous_aliases: set[str] = set()
        for entity in ctx.entities:
            name = str(entity.get("name") or entity.get("canonical") or "")
            canonical = str(entity.get("canonical") or entity.get("canonical_name") or name)
            if self._glossary_url:
                canonical = self._resolve(name or canonical, ctx.domain)
            normalized_name = _identity_key(canonical)
            entity["canonical"] = normalized_name
            canonical_key = _identity_key(canonical)
            explicit_tag = entity.get("tag_id") or entity.get("node_id")
            existing_id = canonical_ids.get(canonical_key)
            if existing_id and not explicit_tag:
                entity["tag_id"] = existing_id
            entity_id = _context_node_id(ctx.domain, entity)
            entity["tag_id"] = entity_id
            if canonical_key not in canonical_ids:
                canonical_ids[canonical_key] = entity_id
            elif explicit_tag and _context_node_id(ctx.domain, {"tag_id": explicit_tag}) != existing_id:
                ambiguous_aliases.add(canonical_key)
            for value in (name, canonical, entity.get("canonical_name"), entity.get("tag_id")):
                if not isinstance(value, str) or not value:
                    continue
                key = _identity_key(value)
                if key in ambiguous_aliases:
                    continue
                previous = mapping.get(key)
                if previous is None:
                    mapping[key] = entity_id
                elif previous != entity_id:
                    mapping[key] = ""
                    ambiguous_aliases.add(key)
        for edge in ctx.entity_edges:
            if edge.get("from_id") is not None or edge.get("to_id") is not None:
                continue
            source = str(edge.get("from") or "")
            target = str(edge.get("to") or "")
            edge["from"] = mapping.get(_identity_key(source)) or source
            edge["to"] = mapping.get(_identity_key(target)) or target
        unique_edges: dict[tuple[str, str, str], dict[str, Any]] = {}
        for edge in ctx.entity_edges:
            source = str(edge.get("from_id") or edge.get("from") or "")
            target = str(edge.get("to_id") or edge.get("to") or "")
            kind = str(edge.get("kind") or edge.get("type") or "RELATED")
            edge_key = (source, target, kind)
            normalized = dict(edge)
            if edge.get("from_id") is None:
                normalized["from"] = source
            if edge.get("to_id") is None:
                normalized["to"] = target
            unique_edges[edge_key] = normalized
        ctx.entity_edges = list(unique_edges.values())

    def _resolve(self, term: str, domain: str) -> str:
        import urllib.request

        headers = {"Content-Type": "application/json"}
        api_key = os.environ.get("AUTH_API_KEY") or os.environ.get("GRAPH_AUTH_API_KEY", "")
        if api_key:
            headers["X-API-Key"] = api_key
        body = json.dumps({"term": term, "domain": domain}).encode("utf-8")
        req = urllib.request.Request(
            f"{self._glossary_url}/api/v1/glossary/resolve",
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=2) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            value = payload.get("canonical_name")
            return str(value) if isinstance(value, str) and value else term
        except (OSError, TimeoutError, json.JSONDecodeError, ValueError):
            return term


class DedupStage(Stage):
    """DEDUP: дедупликация по нормализованному canonical key."""

    name = "DEDUP"

    def run(self, ctx: PipelineContext) -> None:
        canonical_map: dict[str, dict[str, Any]] = {}
        for entity in ctx.entities:
            key = str(entity.get("tag_id") or _identity_key(entity.get("canonical") or entity.get("name") or ""))
            current = canonical_map.get(key)
            if current is None:
                current = dict(entity)
                current["canonical"] = str(
                    entity.get("canonical") or entity.get("canonical_name") or entity.get("name") or key
                )
                current["types"] = _entity_types(entity)
                current["sources"] = _entity_sources(entity)
                current["source_ids"] = list(current["sources"])
                current["chunk_ids"] = _entity_chunk_ids(entity)
                current["variants"] = _entity_variants(entity)
                canonical_map[key] = current
                continue
            current["types"] = list(
                dict.fromkeys([*_entity_types(current), *_entity_types(entity)])
            )
            current["sources"] = list(
                dict.fromkeys([*_entity_sources(current), *_entity_sources(entity)])
            )
            current["source_ids"] = list(current["sources"])
            current["chunk_ids"] = list(
                dict.fromkeys([*_entity_chunk_ids(current), *_entity_chunk_ids(entity)])
            )
            current["variants"] = list(
                dict.fromkeys([*_entity_variants(current), *_entity_variants(entity)])
            )
            current_rank = _origin_rank(current.get("origin"))
            incoming_rank = _origin_rank(entity.get("origin"))
            if incoming_rank >= current_rank:
                for key_name in ("canonical", "canonical_name", "name", "origin", "confidence"):
                    if key_name in entity:
                        current[key_name] = entity[key_name]
                if isinstance(entity.get("properties"), dict):
                    current["properties"] = {
                        **dict(current.get("properties") or {}),
                        **dict(entity["properties"]),
                    }
            for property_name in ("id", "description", "category"):
                if property_name not in current and property_name in entity:
                    current[property_name] = entity[property_name]
            aliases = list(current.get("aliases", []))
            aliases += [value for value in entity.get("aliases", []) if value not in aliases]
            if aliases:
                current["aliases"] = aliases
        ctx.entities = list(canonical_map.values())
        edges: dict[tuple[str, str, str], dict[str, Any]] = {}
        for edge in ctx.entity_edges:
            source = str(edge.get("from_id") or edge.get("from") or "")
            target = str(edge.get("to_id") or edge.get("to") or "")
            kind = str(edge.get("kind") or edge.get("type") or "RELATED")
            edge_key = (source, target, kind)
            normalized = dict(edge)
            if edge.get("from_id") is None:
                normalized["from"] = source
            if edge.get("to_id") is None:
                normalized["to"] = target
            edges[edge_key] = normalized
        ctx.entity_edges = list(edges.values())


class ContractStage(Stage):
    """CONTRACT: склейка иерархии (M1 — заглушка, сохраняет контрактные узлы)."""

    name = "CONTRACT"

    def run(self, ctx: PipelineContext) -> None:
        return None


class ValidateStage(Stage):
    """VALIDATE: структурная валидация сущностей (M1 — базовая)."""

    name = "VALIDATE"

    def run(self, ctx: PipelineContext) -> None:
        for entity in ctx.entities:
            if not entity.get("tag_id") and not entity.get("name") and not entity.get("canonical_name"):
                raise ValueError("context node без tag_id/name на VALIDATE")
            if not entity.get("sources") and not entity.get("source_ids"):
                raise ValueError("context node без provenance на VALIDATE")
        for edge in ctx.entity_edges:
            has_endpoints = all(
                isinstance(edge.get(key), str) and edge[key]
                for key in ("from_id", "to_id")
            ) or all(
                isinstance(edge.get(key), str) and edge[key]
                for key in ("from", "to")
            )
            if not has_endpoints:
                raise ValueError("link без endpoints на VALIDATE")



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


# ------------------------------------------------------------------ ADR-028 retry
# Чтение дефолтов из env — тесты инжектируют значения напрямую в _with_commit_retry,
# валидация env (fail-fast) проверяется через _parse_retry_env.

_log = logging.getLogger("graphrag_proto.orchestrator.commit_retry")

T = TypeVar("T")


def _retry_param_int(name: str, raw: object, *, min_value: int) -> int:
    if not isinstance(raw, str):
        raise TypeError(f"{name}: ожидалось целое число, получено {raw!r}")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name}: ожидалось целое число, получено {raw!r}") from exc
    if value < min_value:
        raise ValueError(f"{name}: не может быть меньше {min_value}, получено {value}")
    return value


def _retry_param_float(name: str, raw: object, *, min_value: float = 0.0) -> float:
    if not isinstance(raw, str):
        raise TypeError(f"{name}: ожидалось число, получено {raw!r}")
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name}: ожидалось число, получено {raw!r}") from exc
    if value < min_value:
        raise ValueError(f"{name}: не может быть меньше {min_value}, получено {value}")
    return value


def _parse_retry_env(env: Mapping[str, str] | None = None) -> tuple[int, float, float]:
    """Чтение и fail-fast-валидация env-параметров retry (ADR-028, спека §2).

    ``N_RETRY_COMMIT`` — число повторов после первой попытки (дефолт 3; ``0`` —
    ровно одна попытка). ``RETRY_BASE_S``/``RETRY_JITTER_S`` — задержки backoff
    (дефолты 0.2 / 0.1 c). Нечисловые и отрицательные значения — ``ValueError``
    (fail-fast при старте), а не молчаливый дефолт (UC12-03).
    """
    src = os.environ if env is None else env
    n_retry = _retry_param_int("N_RETRY_COMMIT", src.get("N_RETRY_COMMIT", "3"), min_value=0)
    base = _retry_param_float("RETRY_BASE_S", src.get("RETRY_BASE_S", "0.2"))
    jitter = _retry_param_float("RETRY_JITTER_S", src.get("RETRY_JITTER_S", "0.1"))
    return n_retry, base, jitter


N_RETRY_COMMIT, RETRY_BASE_S, RETRY_JITTER_S = _parse_retry_env()


def _with_commit_retry(
    stores: list[Any],
    fn: Callable[[], T],
    *,
    attempts: int = N_RETRY_COMMIT + 1,  # spec §2: N_RETRY_COMMIT — число повторов, итого N+1 попытка
    base_delay: float = RETRY_BASE_S,
    jitter: float = RETRY_JITTER_S,
) -> T:
    """Повтор transient-ошибок COMMIT до attempts попыток (ADR-028, S1/S2).

    Стратегия: проверяем `transient_aware()` + `is_transient(exc)` на каждом
    хранилище. При non-transient — немедленный raise без повтора.
    ``attempts`` по умолчанию — ``N_RETRY_COMMIT + 1`` (спецификация
    `concurrent-ingest-write-policy` §2 определяет N_RETRY как число повторов).
    """
    if attempts < 1:
        raise ValueError(
            f"attempts должен быть >= 1 (N_RETRY_COMMIT >= 0), получено {attempts}"
        )
    delay = base_delay
    for attempt in range(attempts):
        try:
            return fn()
        except BaseException as exc:
            transient = any(
                getattr(s, "transient_aware", lambda: False)()
                and getattr(s, "is_transient", lambda _: False)(exc)
                for s in stores
            )
            if not transient or attempt == attempts - 1:
                raise
            _log.warning(
                "transient COMMIT ошибка (попытка %d/%d), повтор через %.2fs: %s",
                attempt + 1,
                attempts,
                delay,
                exc,
            )
            time.sleep(delay + random.uniform(0, jitter))
            delay *= 2
    # unreachable, но mypy доволен
    raise RuntimeError("_with_commit_retry: все попытки исчерпаны")


def _apply_axes(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    vectors: list[dict[str, Any]],
    stale_chunks: list[str],
    graph_tx: Any,
    vector_tx: Any,
    source_domain: str,
    source_url: str,
) -> None:
    """Применение плана записи к контекстам осей (в атомарном пути `graph_tx` и
    `vector_tx` — один объект batch движка)."""
    graph_tx.remove_source_from_entities(source_domain, source_url, stale_chunks)
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
        profile_fetcher: ProfileFetcher | None = None,
        graph_optional: bool = False,
        projection_state_store: ProjectionStateStore | None = None,
    ) -> None:
        self._registry = registry
        self._graph_store = graph_store
        self._vector_store = vector_store
        self._profile_fetcher = profile_fetcher
        self._graph_optional = graph_optional
        self._projection_state_store = projection_state_store

    def try_noop(self, ctx: PipelineContext) -> bool:
        if self._registry is None or ctx.document is None:
            return False
        doc = ctx.document
        with self._registry.source_lock(doc.domain, doc.source_url):
            current = self._registry.latest_active(doc.domain, doc.source_url)
            if current is None or current["content_hash"] != doc.content_hash:
                return False
            if ctx.tags or ctx.links or ctx.metadata:
                return False
            ctx.registry_result = (current["doc_id"], current["version"], False)
            ctx.metadata["revision"] = self._registry.data_revision(doc.domain)
            ctx.graph_projection_status = "pending"
            ctx.commit_applied = True
            ctx.noop = True
            self._record_projection_state(ctx)
            return True

    def run(self, ctx: PipelineContext) -> None:
        if ctx.noop:
            return
        doc = ctx.document
        with self._registry.domain_lock(doc.domain):
            self._run_locked(ctx)

    def _run_locked(self, ctx: PipelineContext) -> None:
        _load_profile(ctx, self._profile_fetcher)
        doc = ctx.document
        with self._registry.source_lock(doc.domain, doc.source_url):
            current = self._registry.latest_active(doc.domain, doc.source_url)
            if current and current["content_hash"] == doc.content_hash and not (
                ctx.tags or ctx.links or ctx.metadata
            ):
                ctx.registry_result = (current["doc_id"], current["version"], False)
                ctx.commit_applied = True
                return
            ctx.metadata["revision"] = self._registry.data_revision_after(doc)
            config_fingerprint = os.environ.get("PROJECTION_CONFIG_FINGERPRINT", "default")
            if self._graph_store is not None:
                ctx.metadata["projection_revision"] = projection_revision_for(
                    str(ctx.metadata["revision"]),
                    config_fingerprint,
                )
            try:
                self._write(doc, ctx)
            except CommitStageError as exc:
                if exc.compensated:
                    self._registry.soft_delete(doc.domain, doc.source_url)
                raise
            except ValueError:
                raise
            except Exception as exc:
                if not self._graph_optional or self._graph_store is None:
                    raise
                ctx.graph_projection_status = "degraded"
                ctx.graph_projection_error = str(exc)
                self._write_vector_only(doc, ctx)
            result = self._registry.upsert(doc)
            ctx.registry_result = result
            ctx.commit_applied = True
            self._record_projection_state(ctx)

    def _record_projection_state(self, ctx: PipelineContext) -> None:
        if self._projection_state_store is None:
            return
        data_revision = str(ctx.metadata.get("revision") or "")
        if not data_revision:
            return
        config_fingerprint = os.environ.get("PROJECTION_CONFIG_FINGERPRINT", "default")
        projection_revision = projection_revision_for(data_revision, config_fingerprint)
        status = ctx.graph_projection_status
        active_source_count = self._registry.active_source_count(ctx.domain)
        if status == "committed":
            state_status = "ready" if active_source_count <= 1 else "pending"
            processed = 1
            failed = 0
        elif status in {"degraded", "disabled"}:
            state_status = "degraded"
            processed = 0
            failed = 1
        else:
            state_status = "pending"
            processed = 0
            failed = 0
        current = self._projection_state_store.get(ctx.domain)
        if current is not None and current.is_ready(data_revision, config_fingerprint):
            return
        if (
            current is not None
            and current.status == "pending"
            and current.lease_until is not None
            and current.lease_until > time.time()
            and current.job_id != f"inline-{ctx.job_id}"
        ):
            return
        if current is not None and not projection_transition_allowed(current, state_status):
            return
        try:
            next_state = ProjectionState(
                domain=ctx.domain,
                data_revision=data_revision,
                projection_revision=projection_revision,
                config_fingerprint=config_fingerprint,
                status=state_status,
                job_id=f"inline-{ctx.job_id}",
                input_source_count=active_source_count,
                processed_source_count=processed,
                skipped_source_count=0,
                failed_source_count=failed,
                started_at="",
                finished_at=None,
                last_error=(
                    ctx.graph_projection_error
                    or ("inline_projection_pending" if state_status == "pending" else None)
                ),
            )
            expected_revision = current.projection_revision if current is not None else None
            self._projection_state_store.compare_and_set(
                ctx.domain,
                expected_revision,
                next_state,
            )
        except Exception as exc:  # noqa: BLE001
            ctx.graph_projection_error = str(exc)

    def _write(self, doc: Any, ctx: PipelineContext) -> None:
        graph = self._graph_store
        vector = self._vector_store
        if graph is None:
            if vector is None:
                raise RuntimeError("COMMIT: vector store is required for baseline")
            self._write_vector_only(doc, ctx)
            return
        if vector is None:
            raise RuntimeError("COMMIT: vector store is required for baseline")
        domain = doc.domain
        source_url = doc.source_url
        source_id = _source_node_id(domain, source_url)
        entities = list(ctx.entities)
        entity_edges = list(ctx.entity_edges)
        alias_ids: dict[str, set[str]] = {}
        for entity in entities:
            entity_id = _context_node_id(domain, entity)
            aliases = entity.get("aliases")
            values = [
                entity.get("canonical_name"),
                entity.get("canonical"),
                entity.get("name"),
                entity.get("tag_id"),
                *(aliases if isinstance(aliases, list) else []),
            ]
            for value in values:
                if isinstance(value, str) and value:
                    alias_ids.setdefault(_identity_key(value), set()).add(entity_id)
        for tag in ctx.tags:
            if not isinstance(tag, dict):
                continue
            tag_id = str(tag.get("tag_id") or tag.get("node_id") or "")
            canonical = str(tag.get("canonical_name") or tag.get("canonical") or tag.get("name") or "")
            if not tag_id and canonical:
                candidates = alias_ids.get(_identity_key(canonical), set())
                tag_id = next(iter(candidates)) if len(candidates) == 1 else _context_node_id(domain, tag)
            if not tag_id:
                continue
            record = dict(tag)
            record.setdefault("tag_id", tag_id)
            record.setdefault("canonical_name", canonical)
            record.setdefault("name", canonical)
            record.setdefault("type", CONTEXT_NODE_LABEL)
            record.setdefault("origin", "user")
            record.setdefault("sources", [source_url])
            record.setdefault("source_ids", [source_url])
            entities.append(record)
            alias_ids.setdefault(_identity_key(canonical), set()).add(tag_id)
        for link in ctx.links:
            if isinstance(link, dict):
                relation = dict(link)
                relation.setdefault("origin", "user")
                relation.setdefault("source_ids", [source_url])
                entity_edges.append(relation)

        all_chunk_ids = [str(meta["chunk_id"]) for meta in ctx.chunks_meta]
        for entity in entities:
            if entity.get("tag_id") and not _entity_chunk_ids(entity):
                entity["chunk_ids"] = list(all_chunk_ids)

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
        entity_ids: dict[str, dict[str, Any]] = {}
        for entity in entities:
            canonical = str(
                entity.get("canonical_name") or entity.get("canonical") or entity.get("name") or ""
            )
            entity_id = _context_node_id(domain, entity)
            entity_ids[entity_id] = entity
            properties: dict[str, Any] = {
                "domain": domain,
                "tag_id": entity_id,
                "canonical_name": canonical,
                "source_ids": _entity_sources(entity) or [source_url],
                "chunk_ids": _entity_chunk_ids(entity),
                "extractor_version": str(entity.get("extractor_version") or EXTRACTOR_VERSION),
                "variants": _entity_variants(entity),
                "properties": dict(entity.get("properties") or {}),
            }
            for key in ("id", "description", "category", "aliases", "origin", "confidence"):
                if key in entity:
                    properties[key] = entity[key]
            nodes.append(
                {
                    "node_id": entity_id,
                    "labels": _entity_types(entity),
                    "properties": properties,
                }
            )

        edges: list[dict[str, Any]] = []
        for relation in entity_edges:
            source = str(relation.get("from_id") or relation.get("from") or "")
            target = str(relation.get("to_id") or relation.get("to") or "")
            if not source or not target:
                raise ValueError("COMMIT: link requires from_id and to_id")
            kind = str(relation.get("kind") or relation.get("type") or "RELATED")
            relation_type = kind.upper() if kind.replace("_", "").isalnum() else "RELATED"
            edge_properties = dict(relation.get("properties") or {})
            edge_properties.setdefault("kind", kind)
            edge_properties.setdefault("domain", domain)
            edge_properties.setdefault("origin", "system")
            edge_properties.setdefault("source_ids", [source_url])
            for key in ("origin", "confidence", "source_ids"):
                if key in relation:
                    edge_properties[key] = relation[key]
            edges.append(
                {
                    "from_id": source,
                    "to_id": target,
                    "type": relation_type,
                    "properties": edge_properties,
                }
            )
        known_ids = set(entity_ids)
        for edge in edges:
            if edge["from_id"] not in known_ids or edge["to_id"] not in known_ids:
                raise ValueError("COMMIT: link references unknown context node")

        vectors: list[dict[str, Any]] = []
        written_chunk_ids = [str(meta["chunk_id"]) for meta in ctx.chunks_meta]
        chunk_id_set = set(written_chunk_ids)
        for meta in ctx.chunks_meta:
            chunk_id = str(meta["chunk_id"])
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
                {
                    "from_id": source_id,
                    "to_id": chunk_id,
                    "type": "CONTAINS",
                    "properties": {
                        "domain": domain,
                        "origin": "system",
                        "source_ids": [source_url],
                    },
                }
            )
            for entity_id, entity in entity_ids.items():
                if chunk_id in _entity_chunk_ids(entity):
                    edges.append(
                        {
                            "from_id": chunk_id,
                            "to_id": entity_id,
                            "type": "MENTIONS",
                            "properties": {
                                "domain": domain,
                                "origin": "system",
                                "source_ids": [source_url],
                            },
                        }
                    )
            vectors.append(
                {
                    "chunk_id": chunk_id,
                    "embedding": meta["embedding"],
                    "metadata": {
                        **dict(ctx.metadata),
                        "text": text,
                        "source_url": source_url,
                        "domain": domain,
                        "index": meta["index"],
                        "context_ids": [
                            entity_id
                            for entity_id, entity in entity_ids.items()
                            if chunk_id in _entity_chunk_ids(entity)
                        ],
                        "tag_ids": [
                            entity_id
                            for entity_id, entity in entity_ids.items()
                            if chunk_id in _entity_chunk_ids(entity)
                        ],
                    },
                }
            )
        if not chunk_id_set.issuperset(
            chunk_id for entity in entities for chunk_id in _entity_chunk_ids(entity)
        ):
            raise ValueError("COMMIT: entity ссылается на неизвестный chunk_id")

        stale_chunks = list(
            dict.fromkeys(
                [
                    *graph.list_chunk_ids_of_source(source_id),
                    *vector.list_chunk_ids_of_source(source_url, domain),
                ]
            )
        )

        # ADR-028: детерминированный порядок — защита от deadlock-циклов (S2, 2.3)
        nodes.sort(key=lambda n: n["node_id"])
        edges.sort(key=lambda e: (e["from_id"], e["to_id"], e["type"]))

        stores = [graph, vector]
        if _is_atomic_pair(graph, vector):
            _with_commit_retry(
                stores,
                lambda: self._write_atomic(
                    graph,
                    vector,
                    nodes,
                    edges,
                    vectors,
                    stale_chunks,
                    domain,
                    source_url,
                ),
            )
            ctx.graph_projection_status = "committed"
            return
        # best_effort: ретраи и компенсация выполняются по осям внутри _write_best_effort
        self._write_best_effort(
            graph,
            vector,
            nodes,
            edges,
            vectors,
            stale_chunks,
            written_chunk_ids,
            domain,
            source_url,
        )
        ctx.graph_projection_status = "committed"

    def _write_vector_only(self, doc: Any, ctx: PipelineContext) -> None:
        vector = self._vector_store
        if vector is None:
            raise RuntimeError("COMMIT: vector store is required for baseline")
        entities = [*ctx.entities, *ctx.tags]
        items: list[dict[str, Any]] = []
        for meta in ctx.chunks_meta:
            chunk_id = str(meta["chunk_id"])
            text = ctx.chunks[meta["index"]]
            context_ids = [
                _context_node_id(doc.domain, entity)
                for entity in entities
                if not _entity_chunk_ids(entity) or chunk_id in _entity_chunk_ids(entity)
            ]
            items.append(
                {
                    "chunk_id": chunk_id,
                    "embedding": meta["embedding"],
                    "metadata": {
                        **dict(ctx.metadata),
                        "text": text,
                        "source_url": doc.source_url,
                        "domain": doc.domain,
                        "index": meta["index"],
                        "context_ids": list(dict.fromkeys(context_ids)),
                        "tag_ids": list(dict.fromkeys(context_ids)),
                    },
                }
            )
        written_ids = {str(meta["chunk_id"]) for meta in ctx.chunks_meta}
        stale_chunks = [
            chunk_id
            for chunk_id in vector.list_chunk_ids_of_source(doc.source_url, doc.domain)
            if chunk_id not in written_ids
        ]
        with vector.transaction() as vector_tx:
            vector_tx.delete_vectors(stale_chunks)
            vector_tx.upsert_vectors(items)
        if ctx.graph_projection_status != "degraded":
            ctx.graph_projection_status = "disabled"

    def _write_atomic(
        self,
        graph: GraphStoreProvider,
        vector: VectorStoreProvider,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        vectors: list[dict[str, Any]],
        stale_chunks: list[str],
        source_domain: str,
        source_url: str,
    ) -> None:
        """Атомарная пара: обе оси в одной транзакции движка (A-2).

        При наличии `atomic_batch()` (Neo4j-пара — один `session.begin_transaction()`)
        — запись через batch; иначе (InMemory-пара, единый процесс) — вложенные
        `transaction()`-контексты: исключение откатывает обе оси.
        """
        batch = graph.atomic_batch()
        if batch is not None:
            with batch as tx:
                _apply_axes(
                    nodes,
                    edges,
                    vectors,
                    stale_chunks,
                    tx,
                    tx,
                    source_domain,
                    source_url,
                )
            return
        with graph.transaction() as graph_tx, vector.transaction() as vector_tx:
            _apply_axes(
                nodes,
                edges,
                vectors,
                stale_chunks,
                graph_tx,
                vector_tx,
                source_domain,
                source_url,
            )

    def _write_best_effort(
        self,
        graph: GraphStoreProvider,
        vector: VectorStoreProvider,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        vectors: list[dict[str, Any]],
        stale_chunks: list[str],
        written_chunk_ids: list[str],
        source_domain: str = "",
        source_url: str = "",
    ) -> None:
        """best_effort-пара (A-2): commit графа, затем вектора.

        ADR-028 §2 (UC12-02): каждая ось оборачивается в retry transient-ошибок
        (`_with_commit_retry`). Компенсация выполняется **только после** того, как
        повторы второй оси исчерпаны (transient) либо сбой второй оси не-transient
        (повторов нет) — иначе граф и вектор остаются рассинхронизированными (L2-03).
        Сбой первой оси — джоба failed без компенсации: транзакция графа откатилась,
        вектор не менялся. Повтор той же джобы идемпотентен (L2-06): registry знает
        версию, контент не изменился → write не выполняется.
        """

        def _graph_axis() -> None:
            with graph.transaction() as graph_tx:
                if source_domain and source_url:
                    graph_tx.remove_source_from_entities(source_domain, source_url, stale_chunks)
                for chunk_id in stale_chunks:
                    graph_tx.delete_node(chunk_id)
                graph_tx.upsert_nodes(nodes)
                graph_tx.upsert_edges(edges)

        def _vector_axis() -> None:
            with vector.transaction() as vector_tx:
                vector_tx.delete_vectors(stale_chunks)
                vector_tx.upsert_vectors(vectors)

        _with_commit_retry([graph], _graph_axis)
        vector_committed = False
        try:
            _with_commit_retry([vector], _vector_axis)
            vector_committed = True
        finally:
            if not vector_committed:
                original = sys.exc_info()[1]
                compensate_ids = sorted(set(stale_chunks) | set(written_chunk_ids))
                _log.warning(
                    "COMMIT best_effort: вторая ось не записана после retry, компенсация "
                    "(удалено чанков: %d): %s",
                    len(compensate_ids),
                    original,
                )
                compensation_ok = False
                try:
                    _compensate(
                        graph,
                        vector,
                        stale_chunks,
                        written_chunk_ids,
                        source_domain,
                        source_url,
                    )
                    compensation_ok = True
                finally:
                    if original is None:
                        raise RuntimeError("COMMIT vector axis failed without an exception")
                    if compensation_ok:
                        raise CommitStageError(
                            "COMMIT best_effort: сбой второй оси, граф компенсирован "
                            f"(удалено чанков: {len(compensate_ids)})",
                            compensated=True,
                        ) from original
                    raise CommitStageError(
                        "COMMIT best_effort: сбой второй оси, компенсация не выполнена",
                        compensated=False,
                    ) from original


def _compensate(
    graph: GraphStoreProvider,
    vector: VectorStoreProvider,
    stale_chunks: list[str],
    written_chunk_ids: list[str],
    source_domain: str = "",
    source_url: str = "",
) -> None:
    """Компенсация best_effort при окончательном отказе второй оси (UC12-02).

    Вызывается из ``_write_best_effort`` только после того, как повторы второй оси
    исчерпаны (transient) или сбой второй оси не-transient — то есть когда запись
    гарантированно не состоится. Первая ось (граф) к этому моменту уже закоммичена,
    поэтому обе оси приводятся к согласованному состоянию: удаляются все записанные
    чанки (stale + новые), орфанов не остаётся ни в одной оси (L2-03).
    """
    compensate_ids = sorted(set(stale_chunks) | set(written_chunk_ids))
    with graph.transaction() as gx:
        if source_domain and source_url:
            gx.remove_source_from_entities(source_domain, source_url, compensate_ids)
        for chunk_id in compensate_ids:
            gx.delete_node(chunk_id)
    vector.delete_vectors(compensate_ids)


def _mark_projection_stale(
    registry: Any,
    projection_state_store: ProjectionStateStore | None,
    domain: str,
) -> None:
    if projection_state_store is None:
        return
    try:
        current = projection_state_store.get(domain)
        data_revision = registry.data_revision(domain) or ""
        if current is None:
            return
        if (
            current.status == "pending"
            and current.lease_until is not None
            and current.lease_until > time.time()
        ):
            return
        config_fingerprint = os.environ.get("PROJECTION_CONFIG_FINGERPRINT", "default")
        next_state = ProjectionState(
            domain=domain,
            data_revision=data_revision,
            projection_revision=projection_revision_for(data_revision, config_fingerprint),
            config_fingerprint=config_fingerprint,
            status="stale",
            job_id=None,
            input_source_count=registry.active_source_count(domain),
            started_at=current.started_at or datetime.now(UTC).isoformat(timespec="seconds"),
            finished_at=datetime.now(UTC).isoformat(timespec="seconds"),
            last_error="source_deleted_rebuild_required",
        )
        if projection_transition_allowed(current, next_state.status):
            projection_state_store.compare_and_set(
                domain,
                current.projection_revision,
                next_state,
            )
    except Exception:  # noqa: BLE001
        return


def soft_delete_source(
    registry: Any,
    graph_store: GraphStoreProvider,
    vector_store: VectorStoreProvider,
    domain: str,
    source_url: str,
    projection_state_store: ProjectionStateStore | None = None,
) -> bool:
    with registry.source_lock(domain, source_url):
        return _soft_delete_source_locked(
            registry,
            graph_store,
            vector_store,
            domain,
            source_url,
            projection_state_store,
        )


def _soft_delete_source_locked(
    registry: Any,
    graph_store: GraphStoreProvider,
    vector_store: VectorStoreProvider,
    domain: str,
    source_url: str,
    projection_state_store: ProjectionStateStore | None = None,
) -> bool:
    """Soft-delete источника (L2-05): чанки снимаются с поиска, сущности остаются.

    Возвращает True, если источник имел активную версию и был помечен deleted.
    ADR-028: запись оборачивается retry (transient-ошибки delete_vectors/транзакций);
    при ОКОНЧАТЕЛЬНОМ отказе удаления в хранилищах реестр возвращается в active
    компенсирующим действием `rollback_soft_delete` (UC12-06, spec §2а) — джоба
    failed не оставляет рассинхрон «реестр deleted, данные в осях остались».
    Повторный вызов после частичного удаления безопасен: удаление идемпотентно
    (L2-06), `list_chunk_ids_of_source` вернёт остаток.
    """
    if not registry.soft_delete(domain, source_url):
        return False

    def _do_delete() -> bool:
        source_id = _source_node_id(domain, source_url)
        chunk_ids = set(graph_store.list_chunk_ids_of_source(source_id))
        chunk_ids.update(vector_store.list_chunk_ids_of_source(source_url, domain))
        ordered_chunk_ids = sorted(chunk_ids)
        with graph_store.transaction() as graph_tx, vector_store.transaction() as vector_tx:
            graph_tx.remove_source_from_entities(domain, source_url, ordered_chunk_ids)
            for chunk_id in ordered_chunk_ids:
                graph_tx.delete_node(chunk_id)
            vector_tx.delete_vectors(ordered_chunk_ids)
        _mark_projection_stale(registry, projection_state_store, domain)
        return True

    try:
        return _with_commit_retry([graph_store, vector_store], _do_delete)
    # BLE001 исключён намеренно: прерывание (KeyboardInterrupt/SystemExit) тоже
    # выравнивает оси — прецедент зафиксирован в review-13 для best-effort.
    except BaseException as exc:
        rolled_back = registry.rollback_soft_delete(domain, source_url)
        _log.warning(
            "soft-delete источника %s не применён после retry; реестр возвращён "
            "в active (rollback_soft_delete=%s): %s",
            source_url,
            rolled_back,
            exc,
        )
        raise


class Analyzer:
    """Оркестратор последовательного прогона этапов."""

    def __init__(self, stages: list[Stage]) -> None:
        self._stages = {s.name: s for s in stages}

    def run(self, ctx: PipelineContext) -> None:
        for name in STAGES:
            self._stages[name].run(ctx)
            if name == "INGEST" and self.try_noop(ctx):
                return

    def run_one(self, name: str, ctx: PipelineContext) -> None:
        if ctx.noop and name not in {"INGEST", "COMMIT"}:
            return
        self._stages[name].run(ctx)

    def try_noop(self, ctx: PipelineContext) -> bool:
        commit_stage = self._stages.get("COMMIT")
        return commit_stage.try_noop(ctx) if commit_stage is not None else False
