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
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
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
from graphrag_proto.ingestion_service.pipeline.profile_contract import (
    PROFILE_PROBLEM_LOG,
    validate_ingestion_profile,
)
from graphrag_proto.ingestion_service.pipeline.prompt_renderer import (
    extraction_identity,
    instruction_fingerprint,
    render_instruction,
)
from graphrag_proto.ingestion_service.projection import (
    ProjectionState,
    ProjectionStateStore,
    projection_revision_for,
    projection_transition_allowed,
)
from graphrag_proto.retrieval.adapters.base import Embedder, GraphStoreProvider, VectorStoreProvider
from graphrag_proto.retrieval.adapters.deterministic import DeterministicEmbedder
from graphrag_proto.retrieval.adapters.llm import (
    LLMAdapterError,
    LLMHTTPError,
    LLMResponseError,
    LLMTimeoutError,
    LLMUnavailableError,
)

#: Вид ручного утверждения. `document` - принадлежит документу и живёт с ним: получает
#: опору на чанки, теряет её при ревизии и убирается как кандидат. `user` - принадлежит
#: пользователю: опоры не получает и переживает ревизию документа, потому что для
#: предиката уборки остаётся структурной. Решение владельца от 2026-09-29, `docs/02` §1.2.
SCOPE_DOCUMENT = "document"
SCOPE_USER = "user"

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
        # Пустой профиль - не «профиля нет», а «его не принесли». Разница выглядит
        # как успех: LLM-экстракция уходит в детерминированный путь с `cause=None`,
        # и по отчёте это читается как «извлечение отработало». Раньше такой отказ
        # обнаруживался только потому, что тест спотыкался о него; в бою он был бы тихим.
        _log.warning(
            "профиль не передан: экстракция уйдёт в детерминированный путь, "
            "домен=%s, источник=%s",
            ctx.domain,
            ctx.source_url,
        )
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
    # Валидация контракта — здесь, а не в Config Service, потому что это единственная
    # общая точка для всех трёх стадий ingest, и потому что ingest может грузить профиль
    # из локального YAML в обход Config Service вообще без проверок.
    #
    # Ошибки НЕ превращаются в исключение: ADR-031 §2 требует, чтобы optional enrichment
    # не блокировал сохранение документа, и «упасть на битом профиле» нарушало бы это.
    # Громкость обеспечивается ERROR-логом, `cause=profile_invalid` и счётчиком — этого
    # хватает, чтобы дефект был виден и не мог проскочить как «просто деградация».
    if not profile:
        # Пустой профиль — законное состояние (профиль не настроен), а не дефект.
        return
    verdict = validate_ingestion_profile(profile)
    if verdict.errors and PROFILE_PROBLEM_LOG.due(ctx.domain, "error", verdict.errors):
        _log.error(
            "профиль домена %r нарушает контракт экстракции: %s — LLM-слой построен не "
            "будет, документ уйдёт в детерминированный fallback (лог один раз на "
            "набор проблем, счётчик — в enrichment_causes отчёта)",
            ctx.domain,
            "; ".join(verdict.errors),
        )
    if verdict.warnings and PROFILE_PROBLEM_LOG.due(ctx.domain, "warning", verdict.warnings):
        _log.warning("профиль домена %r: %s", ctx.domain, "; ".join(verdict.warnings))


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
        raise ExtractionConfigError("в профиле отсутствует extraction.prompt_template")
    if not isinstance(template.get("system"), str):
        raise ExtractionConfigError("extraction.prompt_template должен содержать system")
    # `user` необязателен с 2026-09-29: метод извлечения добавляет генератор инструкции,
    # и требование его здесь означало бы, что профиль обязан дублировать метод в прозе.
    # Заданный, но нестроковый `user` - ошибка, отсутствующий - законен.
    if "user" in template and not isinstance(template.get("user"), str):
        raise ExtractionConfigError("extraction.prompt_template.user задан, но не является строкой")
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
        raise ExtractionModelError("EXTRACT LLM должен вернуть валидный JSON") from exc
    if not isinstance(payload, dict):
        raise ExtractionModelError("EXTRACT LLM должен вернуть JSON-объект")
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


#: Поля записи, из которых берётся имя сущности при проверке концов связи.
#:
#: `id` здесь отсутствует **намеренно и по контракту**: `id` — идентификатор в пределах
#: домена, а не имя. Именно из `id` модель брала концы в прогоне expA, где они оказались
#: выдуманными (`REQ-1..3`, `CON-1..4`) и не разрешились ни один: 12 концов из 12. Включение
#: `id` в список создало бы второй канал именования и сделало бы разрешимость зависящей от
#: того, какое поле модель заполнила, то есть выдумка became бы неотличима от имени.
#: Правильный ремонт — промт (объявить `id` прочитанным из текста, а не назначаемым именем)
#: плюс запись неразрешённого конца фактом вместо сноса слоя (ADR-037).
#:
#: Контракт закреплён тестом `tests/test_grounding_verdicts.py`. Если он когда-нибудь перестанет
#: быть правдой, тест и этот список меняются вместе, а тест не отключается: иначе он станет
#: защитой от исправления.
ENTITY_NAME_FIELDS: tuple[str, ...] = ("tag_id", "canonical", "canonical_name", "name")

#: Поле, которое валидация считает каноническим. Объявлено отдельно от ролей, потому что это
#: единственное утверждение о полях, которое обязано выстоять при правке состава списка.
ENTITY_CANONICAL_FIELD: str = "canonical"

#: Роль каждого поля в соглашении об именах. Нужна рядом со списком, потому что именно из неё
#: берётся **положительная** формулировка контракта: «поле, которое онтология называет
#: каноническим, обязано быть источником имени». Такая формулировка переживает правку состава
#: списка, тогда как проверка «`id` не входит» замораживала бы сегодняшний перечень и первая же
#: правка онтологии упёрлась бы в тест как в ошибку.
ENTITY_FIELD_ROLES: dict[str, str] = {
    "tag_id": "tagger-assigned identifier, used only when the model did not name the entity",
    "canonical": "the name the ontology declares canonical; written by _entity_record from canonical_name or canonical or name",
    "canonical_name": "model-written canonical name; folded into `canonical` by _entity_record",
    "name": "display name; falls back to `canonical` when absent",
}

#: Ключи конца связи и их алиасы — в том порядке, в каком их читает `_validate_edges`:
#: `relation.get("from") or relation.get("from_id")`. Порядок обязателен: при обоих
#: заполненных полях побеждает первое, и счётчик разрешимости обязан считать тот же конец.
#:
#: Список живёт здесь, а не в приборе измерения, потому что прибор играет этот же ответ:
#: своя копия списка означала бы второе соглашение об именах ровно там, где расхождение
#: молчит.
ENDPOINT_KEYS: tuple[tuple[str, ...], ...] = (("from", "from_id"), ("to", "to_id"))

#: Поля ответа модели, которые не являются именем, но в которых модель всё равно писала
#: концы. Нужны для измерения, а не для валидации: их присутствие в ответе означает, что
#: модель назвала сущность не тем полем. `category` добавлен по прогону expD.
NON_NAME_FIELDS: tuple[str, ...] = ("id", "category", "description")


def _entity_names_from_item(item: Mapping[str, Any]) -> tuple[str, str]:
    """`(canonical, name)` из ответа модели — ровно так, как их кладёт `_entity_record`.

    Вынесено отдельно, потому что прибор, считающий разрешимость, обязан разрешать имя
    **тем же** способом. Расхождение двух реализаций уже стоило одного неверного вывода:
    прибор, взявший больше полей, чем валидатор, показал «0 неразрешённых» там, где их было 12.
    """
    canonical = item.get("canonical_name") or item.get("canonical") or item.get("name")
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        name = canonical
    return (
        canonical if isinstance(canonical, str) else "",
        name if isinstance(name, str) else "",
    )


def _declared_names(records: Iterable[Mapping[str, Any]]) -> set[str]:
    """Ключи идентичности имён, которые валидация считает объявленными."""
    known: set[str] = set()
    for record in records:
        for key in ENTITY_NAME_FIELDS:
            value = record.get(key)
            if isinstance(value, str) and value:
                known.add(_identity_key(value))
    return known


def _entity_record(
    entity_type: str,
    item: dict[str, Any],
    ctx: PipelineContext,
    chunk_id: str,
    extractor_version: str,
) -> dict[str, Any]:
    canonical, name = _entity_names_from_item(item)
    if not canonical.strip():
        raise ValueError(f"сущность {entity_type} без канонического имени")
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
    for key in ("id", "description", "category", "confidence"):
        value = item.get(key)
        if key == "description":
            value = _safe_entity_description(value, ctx)
        if value is not None:
            record[key] = value
    # `tag_id` из ответа модели НЕ копируется (L1-05: идентичность узла детерминирована
    # Python-кодом, LLM — только optional enrichment). Промпт его и не запрашивает
    # (`domain_profile.*.yaml`, extraction.prompt_template), то есть поле приходило
    # непрошенным и могло молча создать дубль-ноду вместо слияния по canonical key.
    # Считаем, а не просто игнорируем: частота показывает, путает ли модель
    # идентичность с содержимым, и делает решение обратным по данным, а не по спору.
    if item.get("tag_id"):
        ctx.model_tag_ids_ignored += 1
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

#: Стадии, которые читают профиль. `INGEST` его не читает, а джоба-noop не читает
#: ничего: профиль загружается перед первой из этих, и только если работа не отпала
#: как noop. Отсюда и «раз на джобу» - ровно один вызов фетчера на не-noop джобу.
_PROFILE_CONSUMER_STAGES = frozenset({"CHUNK", "EXTRACT", "COMMIT"})


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
    # Отдельный факт от `enrichment_degraded`: слой не просто «обработан хуже», а
    # ПОТЕРЯН целиком. Флаг ставится в точке сброса, а не в логе, иначе значение
    # пришлось бы вылавливать из текста. Одна метрика на оба факта хуже, чем никакой:
    # профиль не загрузился (ничего не потеряно, LLM и не пытались построить) и
    # исключение в экстракции (потерян весь слой) дают одно и то же «деградация».
    #
    # Ставится по факту исчезнувших записей (см. `_degrade_after_extraction_failure`), а
    # не по факту срабатывания сброса: сброс до первого ответа модели теряет ноль, и
    # такой документ в счётчике потерь был бы ложью.
    llm_layer_dropped: bool = False
    # Сколько записей и рёбер ПОТЕРЯНО сбросом, а сколько LLM-слой произвёл. Счётчик
    # «документов с потерей» отвечает на вопрос «как часто», числа — на вопрос «сколько
    # стоит»: одна потеря — это две записи или две тысячи, и решение о починке
    # принимается по второму.
    llm_layer_lost_entities: int = 0
    llm_layer_lost_edges: int = 0
    llm_records: int = 0
    llm_edges: int = 0
    # Причина деградации (`CAUSE_*`), а не её текст: текст можно переформулировать
    # вместе с причиной, имя — нет.
    enrichment_cause: str | None = None
    extraction_passport: dict[str, Any] | None = None
    # Фактический расход токенов последнего вызова LLM, как его сообщил сервер. Нужен,
    # чтобы отвечать на вопрос «кто жрёт токены - промпт или ответ» по числам, а не по
    # ощущению: раньше расход не снимался вовсе.
    llm_usage: dict[str, Any] | None = None
    # Неразрешённые концы связей (ADR-037, случай 3). Заполняются вместо сноса слоя:
    # каждый элемент — один конец с ИМЕНЕМ, как модель его написала, `chunk_id` и видом
    # связи. Имя хранится исходным, а не ключом идентичности: джоба уборки обязана уметь
    # сопоставить конец с текстом, а ключ для этого бесполезен.
    #
    # Отдельный список, а не флаг: «был неразрешённый конец» и «какие именно» — разные
    # вопросы, и флагом второй не ответить.
    unresolved_endpoints: list[dict[str, Any]] = field(default_factory=list)
    # Счётчики разрешимости (ADR-039). Копятся по ходу EXTRACT, потому что знаменатели —
    # объявленные сущности и концы — считаются там же, где их и разбирали. Пишутся в отчёт
    # на КАЖДОЙ джобе, прошедшей EXTRACT, включая нули: без знаменателя доля не считается,
    # а «концов нет» и «не считали» — разные утверждения.
    resolvability: dict[str, Any] = field(default_factory=dict)
    # Множества имён **на весь документ**. Считать «уникальные» суммой по чанкам нельзя:
    # имя, встретившееся в двух чанках, было бы посчитано дважды, и число уехало бы вверх
    # ровно там, где документ длиннее. Обратная величина, чем кажется: длинный документ дал бы
    # больше «уникальных», хотя уникальных ровно столько же.
    declared_name_keys: set[str] = field(default_factory=set)
    endpoint_keys: set[str] = field(default_factory=set)
    model_tag_ids_ignored: int = 0
    ambiguous_aliases: int = 0
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


class ExtractionConfigError(RuntimeError):
    """Профиль или конфигурация домена не позволяют выполнить экстракцию.

    Это НАШ дефект конфигурации, а не сбой модели: чинить надо профиль, а не LLM.
    Раньше такое приходило как `TypeError` из резолвера, и широкий `except Exception`
    не мог отличить его от сбоя модели — оба выглядели как «LLM не справился», и
    счётчик деградации показывал модель там, где виноват конфиг.
    """


class ExtractionModelError(RuntimeError):
    """Модель или её адаптер вернули негодный результат либо упали.

    Граница намеренно узкая: сюда превращаются только сбои на границе с моделью
    (вызов адаптера, разбор ответа, проверка формы ответа). Дефекты нашего кода
    после этой границы остаются дефектами и падают громко, иначе они надевали бы
    маску «модель не справилась» и уводили расследование не туда.
    """


# Причины деградации, различаемые потребителем. Не перечисление «на вкус»: у них
# разные действия. `profile_unavailable` — профиль не пришёл/сломан, экстракция не
# запускалась, потери нет; `profile_invalid` — профиль есть, но негоден (чинить
# конфиг); `model_error` — модель ответила негодным (чинить промпт/модель).
# `model_unavailable` / `model_timeout` / `model_http_error` — транспорт до модели не
# дошёл: поднять стенд, посмотреть его логи, увеличить таймаут. Раньше все четыре сводились
# в `model_error`, и разбор уводил к провайдеру модели вместо своего стенда.
CAUSE_PROFILE_UNAVAILABLE = "profile_unavailable"
CAUSE_PROFILE_INVALID = "profile_invalid"
CAUSE_MODEL_ERROR = "model_error"
CAUSE_MODEL_UNAVAILABLE = "model_unavailable"
CAUSE_MODEL_TIMEOUT = "model_timeout"
CAUSE_MODEL_HTTP_ERROR = "model_http_error"

#: Транспортная ошибка адаптера → причина деградации. Словарь, а не цепочка `isinstance`,
#: потому что цепочка выросла бы вместе с числом классов и перестала бы читаться.
ADAPTER_CAUSES: dict[type, str] = {
    LLMUnavailableError: CAUSE_MODEL_UNAVAILABLE,
    LLMTimeoutError: CAUSE_MODEL_TIMEOUT,
    LLMHTTPError: CAUSE_MODEL_HTTP_ERROR,
    LLMResponseError: CAUSE_MODEL_ERROR,
}


def _degradation_cause(exc: Exception) -> str:
    """Причина деградации по ТИПУ исключения, а не по его тексту.

    Идёт по цепочке `__cause__`, потому что на границе с моделью исходная транспортная
    ошибка обёрнута в `ExtractionModelError` — классифицировать только внешний тип значило
    бы вернуть `model_error` всем четверым, то есть вернуть ту неразличимость, ради
    устранения которой типы и введены.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        for exc_type, cause in ADAPTER_CAUSES.items():
            if isinstance(current, exc_type):
                return cause
        if isinstance(current, ExtractionConfigError):
            return CAUSE_PROFILE_INVALID
        current = current.__cause__
    return CAUSE_MODEL_ERROR


def _extraction_passport(
    ctx: PipelineContext, llm: Any, instruction: str, identity: str
) -> dict[str, Any]:
    """Паспорт извлечения: чем выполнялся разбор, кроме текста документа.

    **Единица воспроизведения — документ и его версия, а не джоба.** Это решение владельца
    от 2026-09-29, и выбор неочевиден: `job_id` тоже уникален, но воспроизводим только при
    повторе той же джобы, чего на практике не делает никто, а при ретрае он меняется - то
    есть паспорт оказался бы нестабилен ровно там, где нужнее. `source_url` плюс версия
    переживают ретрай, и «переизвлечь этот документ и получить то же» работает.

    Значения, влияющие на извлечение, читаются из адаптера, а не из профиля: `extraction.*`
    в профиле объявлены, но не читаются, а действующие числа живут в блоке `llm`. Записать
    в паспорт профильные означало бы записать неправду - это и есть то расхождение
    (0.1 против 0.3), из-за которого паспорт и понадобился.

    Обращение к приватным полям адаптера осознанно: это единственный источник правды о том,
    с чем он реально пойдёт к серверу, а любые параллельные настройки разъедутся с ним же -
    и паспорт начнёт описывать не то, что выполнялось.
    """
    profile = ctx.profile or {}
    raw_meta = profile.get("profile")
    meta = raw_meta if isinstance(raw_meta, Mapping) else {}
    return {
        "domain": ctx.domain,
        "source_url": ctx.source_url,
        "document_version": int(getattr(ctx, "document_version", 0) or 0),
        "profile": f"{meta.get('name') or '?'!s}@{meta.get('version') or '0'!s}",
        "llm_enabled": _profile_llm_enabled(profile),
        "model": str(getattr(llm, "_model", "") or ""),
        "temperature": getattr(llm, "_temperature", ""),
        "max_tokens": getattr(llm, "_max_tokens", ""),
        "context_window": getattr(llm, "_context_window", "") or "не объявлено стендом",
        "timeout_s": getattr(llm, "_timeout_s", ""),
        "seed": getattr(llm, "_seed", None) or "не задан (temperature=0 делает его ненужным)",
        "instruction_fingerprint": instruction_fingerprint(instruction),
        "identity": identity,
        # Фактический расход токенов, если сервер его сообщил. Это единственное место, где
        # видно, во сколько обошёлся разбор: без него паспорт описывает, ЧЕМ выполнялось,
        # но не СКОЛЬКО стоило, и вопрос «кто жрёт токены» не имеет ответа по числам.
        "usage": dict(ctx.llm_usage) if isinstance(ctx.llm_usage, dict) and ctx.llm_usage else None,
        # Инструкция целиком: она и есть переиспользуемая часть, текст чанков уже в `Chunk`.
        "instruction": instruction,
    }


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
            # Профиль не пришёл — экстракция не запускалась, ничего не потеряно.
            # Отдельная причина, потому что «деградация» без потери и «деградация с
            # потерей» лечатся противоположно.
            ctx.enrichment_cause = CAUSE_PROFILE_UNAVAILABLE
        if not ctx.profile or not _profile_llm_enabled(ctx.profile) or self._llm is None:
            self._extract_deterministic(ctx)
            return
        if not self._optional_failure:
            self._extract_llm(ctx)
            self._record_llm_volume(ctx)
            return
        try:
            self._extract_llm(ctx)
        except (ExtractionConfigError, ExtractionModelError) as exc:
            self._degrade_after_extraction_failure(ctx, exc)
            return
        self._record_llm_volume(ctx)

    @staticmethod
    def _record_llm_volume(ctx: PipelineContext) -> None:
        """Зафиксировать объём LLM-слоя там, где он вычислен (ADR-033, п. 2).

        Значение эмитится здесь, а не восстанавливается потребителем из строк БД или
        из текста сообщения: восстановленное значение однажды разъедется с реальностью
        без всякого следа.
        """
        ctx.llm_records = len(ctx.entities)
        ctx.llm_edges = len(ctx.entity_edges)

    def _degrade_after_extraction_failure(self, ctx: PipelineContext, exc: Exception) -> None:
        """Сбросить LLM-слой и честно сказать, ЧТО именно исчезло.

        ПРОТОТИПНОЕ ПОВЕДЕНИЕ, для боевого ingest неприемлемо (ADR-032): потеря
        необратима, повторная загрузка того же `content_hash` — no-op (см.
        CommitStage.try_noop), то есть простая перезагрузка файла НЕ перезапустит
        экстракцию.

        Ловим ровно два класса: негодную конфигурацию и сбой на границе с моделью. Широкий
        `except Exception` больше не используется — дефект нашего кода обязан упасть
        громко, иначе он выдаёт себя за «модель не справилась» и уводит
        расследование не туда, а счётчик деградации показывает чужую причину.

        Причина выбирается по типу, а не по тексту: недоступный стенд, таймаут, код
        ошибки и негодный ответ — четыре разных ремонта, и раньше они были одной строкой.
        """
        # Сколько записей и рёбер ПЕРЕЖИВАЛИ бы сброс: это и есть потеря. Пока
        # считаем ДО очистки — иначе потеря всегда выглядит нулевой.
        lost_entities = len(ctx.entities)
        lost_edges = len(ctx.entity_edges)
        ctx.entities = []
        ctx.entity_edges = []
        ctx.enrichment_degraded = True
        ctx.enrichment_cause = _degradation_cause(exc)
        ctx.enrichment_error = str(exc)
        # Объём LLM-слоя известен даже при сбое: то, что модель успела вернуть, и есть
        # то, что мы сейчас уничтожаем.
        ctx.llm_records = lost_entities
        ctx.llm_edges = lost_edges
        ctx.llm_layer_lost_entities = lost_entities
        ctx.llm_layer_lost_edges = lost_edges
        # Сброс без записей — не потеря. Флаг «слой потерян» ставится по факту
        # исчезнувших фактов, а не по факту сработавшего пути сброса: иначе
        # счётчик завышает потерю на документах, где терять было нечего, и его
        # перестают читать.
        ctx.llm_layer_dropped = bool(lost_entities or lost_edges)
        self._extract_deterministic(ctx)

    def _extract_llm(self, ctx: PipelineContext) -> None:
        template = _extraction_template(ctx.profile or {})
        # Схема в инструкции не пишется руками: она генерируется из профиля, иначе вид
        # связи, добавленный в `ontology.edge_types` и забытый в прозе, оказывается
        # объявленным и никогда не извлекаемым - и выглядит как «модель не выдаёт связи».
        # `prompt_id` остаётся человекочитаемой подписью в отчёте, но идентичностью
        # извлечения не является: версия считается от содержимого инструкции.
        instruction = render_instruction(ctx.profile or {}, str(template.get("user") or ""))
        extractor_version = extraction_identity(ctx.profile or {}, instruction)
        ctx.extraction_passport = _extraction_passport(ctx, self._llm, instruction, extractor_version)
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
            raise ExtractionConfigError("EXTRACT: LLM adapter is required for profile extraction")
        for index, chunk in enumerate(ctx.chunks):
            chunk_id = self._chunk_id(ctx, index)
            prompt = (
                f"{instruction}\n\n"
                f"Текст документа для извлечения (чанк {index + 1}):\n{chunk}"
            )
            # Граница с моделью: ровно здесь всё, что пришло извне, становится
            # `ExtractionModelError`. Внутри блока нет нашей логики, поэтому
            # классификация здесь не съест наш дефект — а за пределами блока такой
            # дефект падает сам, вместо того чтобы выдать себя за сбой модели.
            #
            # Тип транспортной ошибки СОХРАНЯЕТСЯ (`from exc`), иначе классификация ниже
            # не сможет отличить «стенд не отвечает» от «модель ответила ерундой».
            try:
                raw = "".join(
                    llm.generate(
                        prompt,
                        system=str(template["system"]),
                        stream=False,
                        # Идентификаторы, а не текст: по ним запись обмена соединяется с
                        # документом и чанком. Текст чанка в журнал не пишется.
                        labels={
                            "source_url": str(ctx.source_url),
                            "chunk_id": str(chunk_id),
                        },
                    )
                )
            except LLMAdapterError as exc:
                # Улика едет в текст ошибки, а не теряется: `ExtractionModelError` попадает
                # в запись джобы, и по нему видно, что модель успела наговорить до обрыва.
                # Без этого вопрос «что именно модель пишет в ответ» был неотвечаемым.
                detail = getattr(exc, "raw", None)
                raise ExtractionModelError(
                    f"вызов LLM не удался: {exc}"
                    + (f"\n--- ответ модели (усечён) ---\n{detail}" if detail else "")
                ) from exc
            except Exception as exc:  # граница с внешним адаптером, ловится намеренно
                raise ExtractionModelError(f"вызов LLM не удался: {exc}") from exc
            usage = getattr(llm, "last_usage", None)
            if isinstance(usage, dict) and usage:
                ctx.llm_usage = usage
            payload = _parse_extraction_payload(raw)
            records: list[dict[str, Any]] = []
            unknown_fields = set(payload) - allowed_fields
            if unknown_fields:
                raise ExtractionModelError(
                    f"EXTRACT содержит неизвестные поля: {sorted(unknown_fields)}"
                )
            for key, entity_type in entity_payloads.items():
                items = payload.get(key, [])
                if not isinstance(items, list):
                    raise ExtractionModelError(f"EXTRACT поле {key!r} должно быть списком")
                for item in items:
                    if not isinstance(item, dict):
                        raise ExtractionModelError(f"EXTRACT поле {key!r} содержит не-объект")
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
            unresolved = self._validate_edges(relations, records)
            # Индекс по КЛЮЧИ неразрешённого имени — тем же `_identity_key`, каким
            # пользуется валидация. Сопоставление по сырой строке было бы вторым,
            # несовпадающим с кодом соглашением об именах, то есть ровно тем дефектом,
            # который прибор уже ловил дважды.
            unresolved_by_key = {
                _identity_key(item["endpoint_name"]) for item in unresolved
            }
            ctx.entities.extend(records)
            ctx.unresolved_endpoints.extend(
                {
                    **item,
                    "chunk_id": chunk_id,
                    "endpoint_key": _identity_key(item["endpoint_name"]),
                }
                for item in unresolved
            )
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
                # Происхождение ставит платформа, а не модель: промпт прямо запрещает
                # `chunk_ids` в relationships, потому что концы задаются именами. Но связь —
                # утверждение, и у него должна быть опора, иначе её нечем чистить: предикат
                # отбора опирается на `chunk_ids` (`docs/02` §4.5), и без них ни одна доменная
                # связь не является кандидатом на уборку. Экстракция почанковая, и нужный
                # `chunk_id` здесь в области видимости.
                edge.setdefault("source_ids", [ctx.source_url])
                edge["chunk_ids"] = [chunk_id]
                # Неразрешённый конец: строка связи СОХРАНЯЕТСЯ с пометкой, иначе теряются
                # `kind` и `confidence` — то есть ровно то, ради чего извлечение и делалось
                # (ADR-038). Факт о неизвестном имени пишется отдельно, исходным именем.
                if unresolved_by_key:
                    missing = [
                        side
                        for side, keys in (("from", ("from", "from_id")), ("to", ("to", "to_id")))
                        if any(
                            _identity_key(relation.get(key) or "") in unresolved_by_key
                            for key in keys
                            if relation.get(key)
                        )
                    ]
                    if missing:
                        edge["endpoint_unresolved"] = True
                        edge["unresolved_sides"] = missing
                ctx.entity_edges.append(edge)
            self._accumulate_resolvability(
                ctx, records, relations, unresolved_by_key, unresolved
            )
        if ctx.model_tag_ids_ignored:
            # Одна строка на документ, а не на сущность: при ~77 сущностях на чанк
            # поштучный лог дал бы десятки тысяч строк на документ.
            _log.warning(
                "tag_id в ответе LLM проигнорирован: идентичность считает платформа "
                "(L1-05); проигнорировано=%d, источник=%s",
                ctx.model_tag_ids_ignored,
                ctx.source_url,
            )

    @staticmethod
    def _accumulate_resolvability(
        ctx: PipelineContext,
        records: list[dict[str, Any]],
        relations: list[dict[str, Any]],
        unresolved_by_key: set[str],
        unresolved: list[dict[str, Any]],
    ) -> None:
        """Копнуть счётчики разрешимости по чанку (ADR-039).

        Считаются **слоты концов**, а не связи: у связи два конца, и в expA все шесть потеряли
        оба. Считать по связям удвоило долю, и числитель при этом был верным — то есть ошибка
        пережила пересчёт.

        Неразрешённые концы считаются и по слотам, и по именам, потому что это разные
        величины: один и тот же конец встречается в нескольких слотах (expA — 12 и 7), и
        агрегат по джобам суммирует именно слоты.

        Структура (петли, взаимные пары, входящая связность) копится по тем же рёбрам и
        от версии кода не зависит.
        """
        stats = ctx.resolvability
        stats["chunks"] = int(stats.get("chunks") or 0) + 1
        stats["entities_declared"] = int(stats.get("entities_declared") or 0) + len(records)
        stats["relations"] = int(stats.get("relations") or 0) + len(relations)
        ctx.declared_name_keys |= _declared_names(records)

        declared_keys = _declared_names(records)
        slot_keys: list[str] = []
        for relation in relations:
            for keys in ENDPOINT_KEYS:
                for key in keys:
                    value = relation.get(key)
                    if isinstance(value, str) and value.strip():
                        slot_keys.append(_identity_key(value.strip()))
                        break
        stats["endpoints_total"] = int(stats.get("endpoints_total") or 0) + len(slot_keys)
        resolved_slots = sum(1 for key in slot_keys if key in declared_keys)
        stats["endpoints_resolved"] = int(stats.get("endpoints_resolved") or 0) + resolved_slots
        stats["endpoints_unresolved"] = int(stats.get("endpoints_unresolved") or 0) + (
            len(slot_keys) - resolved_slots
        )
        ctx.endpoint_keys.update(slot_keys)
        unresolved_names: set[str] = set()
        stats["names_unresolved"] = int(stats.get("names_unresolved") or 0) + len(
            unresolved_by_key
        )
        unresolved_names |= unresolved_by_key
        stats["names_resolved"] = int(stats.get("names_resolved") or 0) + len(
            {key for key in slot_keys} - unresolved_by_key
        )

        # Из какого поля модель взяла неразрешённое имя. Ключ -> поле, а не счётчик попаданий:
        # уклон в одно поле и разнобой — разные дефекты с разными владельцами, и одно число их
        # не различает. Сравнение по `_identity_key`, тем же, что у валидации.
        by_field = dict(stats.get("by_field") or {})
        non_name: dict[str, set[str]] = {}
        for record in records:
            for non_name_field in NON_NAME_FIELDS:
                value = record.get(non_name_field)
                if isinstance(value, str) and value.strip():
                    non_name.setdefault(_identity_key(value.strip()), set()).add(non_name_field)
        for fact in unresolved:
            name_key = _identity_key(str(fact.get("endpoint_name") or ""))
            for source_field in non_name.get(name_key, ()):
                by_field[source_field] = int(by_field.get(source_field, 0)) + 1
        stats["by_field"] = by_field

        pairs = [
            (
                _identity_key(str(relation.get("from") or relation.get("from_id") or "")),
                _identity_key(str(relation.get("to") or relation.get("to_id") or "")),
            )
            for relation in relations
            if (relation.get("from") or relation.get("from_id"))
            and (relation.get("to") or relation.get("to_id"))
        ]
        loops = {a for a, b in pairs if a == b}
        stats["self_loops"] = int(stats.get("self_loops") or 0) + len(loops)
        mutual = 0
        for index, (a, b) in enumerate(pairs):
            if a == b:
                continue
            for other in pairs[index + 1 :]:
                if a == other[1] and b == other[0]:
                    mutual += 1
        stats["mutual_pairs"] = int(stats.get("mutual_pairs") or 0) + mutual
        fan_in = Counter(target for _, target in pairs)
        if fan_in:
            peak = max(fan_in.values())
            if peak > int(stats.get("max_fan_in") or 0):
                stats["max_fan_in"] = peak
        # «Уникальные» считаются на документ целиком, а не суммой по чанкам.
        stats["entities_distinct"] = len(ctx.declared_name_keys)
        stats["endpoints_distinct"] = len(ctx.endpoint_keys)

    def _chunk_id(self, ctx: PipelineContext, index: int) -> str:
        for meta in ctx.chunks_meta:
            if meta.get("index") == index:
                return str(meta["chunk_id"])
        return _chunk_id(ctx.domain, ctx.source_url, index)

    @staticmethod
    def _validate_edges(
        relations: object,
        records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Проверить концы связей и вернуть неразрешённые (ADR-037, случай 3).

        Неразрешённый конец **не сносит слой**. Он возвращается наружу, чтобы вызывающий
        записал факт; раньше здесь поднималось `ExtractionModelError`, и одна такая связь
        уничтожала весь LLM-слой документа — вместе с `kind` и `confidence` тех связей,
        которые были в порядке. Проверка «объявлено ли имя среди имён этого ответа»
        продолжает работать ровно так же: она никуда не делась, изменилось то, что
        происходит при её неудаче.

        Форма негодного ответа (не список, не объект, пустой конец) по-прежнему
        поднимает `ExtractionModelError`: это не вопрос разрешимости, это негодный JSON,
        и притворяться, что мы его «записали фактом», нельзя.
        """
        if not isinstance(relations, list):
            raise ExtractionModelError("EXTRACT поле relationships должно быть списком")
        known: set[str] = _declared_names(records)
        unresolved: list[dict[str, Any]] = []
        for relation in relations:
            if not isinstance(relation, dict):
                raise ExtractionModelError("EXTRACT relationship должен быть объектом")
            source = relation.get("from") or relation.get("from_id")
            target = relation.get("to") or relation.get("to_id")
            if not isinstance(source, str) or not source or not isinstance(target, str) or not target:
                raise ExtractionModelError("relationship должен содержать from и to")
            missing = [
                side
                for side, value in (("from", source), ("to", target))
                if _identity_key(value) not in known
            ]
            kind = str(relation.get("kind") or relation.get("type") or "RELATED")
            # По одному факту на КАЖДЫЙ неразрешённый конец. Один факт на связь выглядел бы
            # экономнее, но если неразрешённых концов два, второй потерял бы имя — и джоба
            # уборки не смогла бы его сопоставить ни с чем.
            for side, name, other in (("from", source, target), ("to", target, source)):
                if side in missing:
                    unresolved.append(
                        {
                            "unresolved_sides": missing,
                            "unresolved_side": side,
                            "endpoint_name": name,
                            "other_endpoint_name": other,
                            "relation_kind": kind,
                        }
                    )
        return unresolved

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
        # L3-02: неоднозначные варианты не объединяются молча — алиасы для них
        # подавлены выше. Но «не молча» про слияние, а не про наблюдаемость: без
        # этого счётчика нельзя отличить документ без неоднозначности от документа,
        # где их были десятки и молча выбросили. UNIQUE-constraint этого не даст —
        # он умеет только слить или отвергнуть (ADR-031 §4).
        ctx.ambiguous_aliases = len(ambiguous_aliases)
        if ctx.ambiguous_aliases:
            _log.warning(
                "NORMALIZE: неоднозначные ключи не объединены (L3-02): ключей=%d, "
                "алиасы для них подавлены, источник=%s",
                ctx.ambiguous_aliases,
                ctx.source_url,
            )
        for edge in ctx.entity_edges:
            # Каждый конец переводится независимо. Раньше стоял `continue` на всю связь,
            # если непуст **любой** из `from_id`/`to_id`, и это ломало смешанную форму
            # `from` + `to` + `to_id`, которую модель отдаёт законно: `to_id` есть, значит
            # стадия решила, что переводить нечего, и оставила `from` сырым именем. Дальше
            # COMMIT сравнивал имя с множеством `tag:it:...` и валил весь документ
            # (`docs/api_reference.md`: `from_id='Query API Contract'` при 38 узлах).
            for name_key, id_key in (("from", "from_id"), ("to", "to_id")):
                if edge.get(id_key) is not None:
                    continue
                value = str(edge.get(name_key) or "")
                if not value:
                    continue
                edge[name_key] = mapping.get(_identity_key(value)) or value
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
                # `scope` различает два вида ручных утверждений (решение владельца
                # 2026-09-29). По умолчанию связь документная: она пришла с загрузкой и
                # держится на этом документе. Явный `scope: "user"` - утверждение,
                # принадлежащее пользователю, а не документу.
                #
                # Различие не выдумано, а следует из уже существующей механики. Документная
                # связь получает в опору чанки документа, и тогда `chunk_ids IS NOT NULL`
                # делает её кандидатом на уборку. Пользовательская не получает ничьей опоры
                # и, что важнее, не претендует на `source_ids` документа: `_remove_source`
                # снимает происхождение по этому списку, и будь он там - ревизия удалила бы
                # пользовательское утверждение вместе с документом, то есть различие было бы
                # только наполовину и выглядело бы работающим.
                relation.setdefault("scope", SCOPE_DOCUMENT)
                if relation["scope"] != SCOPE_USER:
                    relation.setdefault("source_ids", [source_url])
                entity_edges.append(relation)

        all_chunk_ids = [str(meta["chunk_id"]) for meta in ctx.chunks_meta]
        for entity in entities:
            if entity.get("tag_id") and not _entity_chunk_ids(entity):
                entity["chunk_ids"] = list(all_chunk_ids)
        # Связь — такое же утверждение в этом документе, как тег, и получает опору здесь
        # же. Раньше `chunk_ids` проставлялись только сущностям, и ручная связь оставалась
        # неотличимой от структурной по предикату `chunk_ids IS NOT NULL`, то есть
        # неубираемой навсегда (`docs/02` §4.5, дыра зафиксирована там же). Уже
        # проставленную опору не трогаем: у извлечённой связи она своя, почанковая.
        for relation in entity_edges:
            # Пользовательское утверждение опоры не получает: оно не привязано к документу,
            # и `chunk_ids IS NULL` делает его структурным для предиката уборки, то есть
            # переживающим ревизию. Документная связь, включая любую извлечённую, опору
            # получает - иначе предикат её не увидит.
            if relation.get("scope") == SCOPE_USER:
                continue
            if not relation.get("chunk_ids"):
                relation["chunk_ids"] = list(all_chunk_ids)

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
        skipped_unresolved = 0
        for relation in entity_edges:
            source = str(relation.get("from_id") or relation.get("from") or "")
            target = str(relation.get("to_id") or relation.get("to") or "")
            if not source or not target:
                raise ValueError("COMMIT: link requires from_id and to_id")
            # Связь с неразрешённым концом в граф НЕ пишется (ADR-038, п. 3): её второй
            # конец не существует как нода, и обход упёрся бы в висящий конец. Раньше
            # здесь был `raise`, и одна такая связь валила весь документ на COMMIT — то
            # есть пункт 3 ADR-037 переносил потерю из EXTRACT в COMMIT, не устраняя её.
            # Строка связи и факт о неразрешённости остаются в контексте и в отчёте джобы,
            # то есть `kind` и `confidence` не теряются — они не попадают в обход.
            if relation.get("endpoint_unresolved"):
                skipped_unresolved += 1
                continue
            kind = str(relation.get("kind") or relation.get("type") or "RELATED")
            relation_type = kind.upper() if kind.replace("_", "").isalnum() else "RELATED"
            edge_properties = dict(relation.get("properties") or {})
            edge_properties.setdefault("kind", kind)
            if relation.get("scope"):
                edge_properties.setdefault("scope", str(relation["scope"]))
            edge_properties.setdefault("domain", domain)
            edge_properties.setdefault("origin", "system")
            # Пользовательское утверждение не претендует на документ, поэтому и
            # `source_ids` документа ему не достаётся: `_remove_source` снимает
            # происхождение по этому списку, и будь он там - ревизия удалила бы связь,
            # которая должна пережить документ.
            if relation.get("scope") != SCOPE_USER:
                edge_properties.setdefault("source_ids", [source_url])
            for key in ("origin", "confidence", "source_ids"):
                if key in relation:
                    edge_properties[key] = relation[key]
            if relation.get("chunk_ids"):
                edge_properties["chunk_ids"] = list(relation["chunk_ids"])
            # `chunk_ids` связи - опора, на которой держится весь предикат уборки
            # (`docs/02` §4.5): без неё связь выглядит структурной (`chunk_ids IS NULL`),
            # а такой класс уборка обязана не трогать. Копируется только непустое значение:
            # у связи, которой нечем подтвердить опору, свойства `chunk_ids` не должно быть
            # вовсе, иначе она станет кандидатом на удаление, то есть потеряет данные.
            #
            # Пропуск этого присваивания стоил дороже всего: связи создавались без опоры,
            # предикат не видел ни одной, и уборка была пустым упражнением на любом корпусе.
            # Нашёл это E2E-прогон (`test_artifacts/e2e-cleanup/`), а не тесты: тесты
            # строиили рёбра руками, уже с `chunk_ids` в свойствах, либо проверяли защиту
            # связи, которая как раз и не была кандидатом.
            edges.append(
                {
                    "from_id": source,
                    "to_id": target,
                    "type": relation_type,
                    # Имена концов сохраняются для диагностики: `from_id`/`to_id` к
                    # этому моменту уже могут быть идентификаторами, и по ним одним
                    # не отличить «модель дала имя» от «сущность не дожила».
                    "from_name": relation.get("from"),
                    "to_name": relation.get("to"),
                    "properties": edge_properties,
                }
            )
        known_ids = set(entity_ids)
        # Последний шанс перевести конец в идентификатор. NORMALIZE это уже делает, но
        # сущность могла не дожить до COMMIT (слияние, подавленный неоднозначный алиас),
        # и тогда имя осталось бы именем. Ключ здесь единственное место, где оба конца
        # сравниваются с одним и тем же множеством, поэтому перевод делается тут.
        for edge in edges:
            for id_key, name_key in (("from_id", "from_name"), ("to_id", "to_name")):
                value = edge[id_key]
                if value in known_ids:
                    continue
                resolved = entity_ids.get(_context_node_id(domain, {"canonical_name": value}))
                if resolved is not None:
                    edge[id_key] = resolved
        dropped_edges: list[dict[str, Any]] = []
        for edge in edges:
            if edge["from_id"] in known_ids and edge["to_id"] in known_ids:
                continue
            # Решение владельца от 2026-10-02: конец вне идентификаторов - это факт
            # (ADR-037), а не гибель документа. Раньше здесь был `raise`, и одна
            # непереведённая связь валила весь документ на COMMIT.
            missing = [
                side
                for side, value in (("from_id", edge["from_id"]), ("to_id", edge["to_id"]))
                if value not in known_ids
            ]
            dropped_edges.append(
                {
                    "endpoint_name": str(edge[missing[0]]),
                    "endpoint_key": _identity_key(str(edge[missing[0]])),
                    "relation_kind": str(edge.get("kind") or edge["type"]),
                    "other_endpoint_name": str(
                        edge["to_name"] if missing[0] == "from_id" else edge["from_name"]
                    ),
                    "stage": "COMMIT",
                }
            )
        if dropped_edges:
            edges = [
                edge
                for edge in edges
                if edge["from_id"] in known_ids and edge["to_id"] in known_ids
            ]
            # Счётчик не теряется: молчание здесь стоило недели работы. Пустой граф даёт
            # `necessity = 0`, который читается как «граф не нужен», а не как «связи не
            # записались». Поэтому потеря попадает и в лог, и в счётчик разрешимости.
            ctx.unresolved_endpoints.extend(dropped_edges)
            skipped_unresolved += len(dropped_edges)
            _log.error(
                "COMMIT: связи с концом вне идентификаторов не записаны: %d, источник=%s, "
                "концы=%s",
                len(dropped_edges),
                source_url,
                [item["endpoint_name"] for item in dropped_edges],
            )
        if skipped_unresolved:
            # Видно на логе, а не только в таблице: «связь не записана» и «связи не было»
            # — разные утверждения, и второе неверно.
            #
            # Уровень ERROR и сами концы — решение владельца от 2026-10-02. INFO с одним
            # счётчиком прятал потерю: в прогоне `docs/api_reference.md` молча не записалось
            # 29 связей из 32, и в отчёте это выглядело как успех. Молчание здесь опаснее
            # всего: пустой граф даёт `necessity = 0`, который читается как «граф не нужен».
            _log.error(
                "связи с неразрешённым концом не записаны в граф: %d, источник=%s, концы=%s",
                skipped_unresolved,
                source_url,
                [fact.get("endpoint_name") for fact in ctx.unresolved_endpoints],
            )

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
    """Полный конвейер джобы: стадии по `STAGES`."""
    def __init__(self, stages: list[Stage], profile_fetcher: ProfileFetcher | None = None) -> None:
        self._stages = {s.name: s for s in stages}
        # Профиль - свойство прогона, а не сборки стадий. Его грузили три стадии
        # (CHUNK, EXTRACT, COMMIT), и каждая держала собственный фетчер, а
        # `_load_profile` выходил по `ctx.profile_loaded`, то есть авторитетом
        # становился **кто позвал первым**. Связка в `app.py` была правильной - один
        # `profile_loader.load` на все три, - но свойство держалось на ней, а не на коде:
        # стадия без фетчера замораживала `profile={}`, и LLM-экстракция молча
        # выключалась с `cause=None`. Теперь грузит Analyzer, один раз, до стадий.
        self._profile_fetcher = profile_fetcher

    def run(self, ctx: PipelineContext) -> None:
        for name in STAGES:
            self._prepare_stage(name, ctx)
            self._stages[name].run(ctx)
            if name == "INGEST" and self.try_noop(ctx):
                return

    def run_one(self, name: str, ctx: PipelineContext) -> None:
        # Тот же профиль, что и в `run`: `Executor` идёт по стадиям через `run_one`, и
        # грузил его только `run` - путь сервиса остался бы без профиля при полностью
        # правильной сборке. Инициализация общая, иначе это свойство держится на том,
        # какой из двух путей позвали.
        self._prepare_stage(name, ctx)
        if ctx.noop and name not in {"INGEST", "COMMIT"}:
            return
        self._stages[name].run(ctx)

    def _prepare_stage(self, name: str, ctx: PipelineContext) -> None:
        """Грузит профиль перед первой стадией, которая его читает.

        Не раньше и не при любой стадии: `INGEST` профиль не читает, а джоба, оказавшаяся
        noop, не читает его вообще. Из-за безусловной загрузки на каждой стадии повторная
        джоба на тот же `source_url` грузила профиль второй раз, и проверка «профиль
        грузится раз на джобу» стала зависеть от порядка стадий, а не от решения.
        """
        if name in _PROFILE_CONSUMER_STAGES and not ctx.noop:
            _load_profile(ctx, self._profile_fetcher)

    def try_noop(self, ctx: PipelineContext) -> bool:
        commit_stage = self._stages.get("COMMIT")
        return commit_stage.try_noop(ctx) if commit_stage is not None else False
