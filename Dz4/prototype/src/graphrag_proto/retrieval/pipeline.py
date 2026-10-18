"""QueryPipeline — 7 шагов retrieval-цикла (docs/03_retriever.md, D3 design M2):

1. embedding (DeterministicEmbedder, L4-01) → status: embedding
2. semantic cache (бандл 3/3, add-semantic-cache): hit → status: cache{hit:true} → done
   (без store/LLM, без token); miss → обычный цикл + store() перед done
3. baseline: vector search с metadata; optional graph experiment выполняет bounded expansion
   только при `graph_search_enabled=true` и готовой projection
4. rerank (NoOp) → status: rerank
5. Context Assembly (context.py, L3-03/L3-04)
6. LLM streaming (OpenAICompatibleAdapter / FakeLLM) → status: llm, token*
7. done — {text, sources, generation_time_s, retrieval_time_s, total_time_s, revision[, cache_hit, cache_lookup_s]}

emit(event_type, payload) — обратный вызов воркера (публикует события конверта ADR-016).
`revision` — fingerprint ревизии данных домена (ADR-026): в любом исходе (в т.ч.
cache-hit и при генерации) — «по каким данным собран ответ», срез для Eval (ADR-015).
"""

from __future__ import annotations

import builtins
import hashlib
import json
import logging
import os
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from graphrag_proto.ingestion_service.pipeline.profile_contract import (
    PROFILE_PROBLEM_LOG,
    validate_retrieval_profile,
)
from graphrag_proto.ingestion_service.projection import (
    ProjectionMetrics,
    ProjectionState,
    ProjectionStateStore,
)
from graphrag_proto.retrieval.adapters.base import (
    OWNERLESS_SOURCE,
    Embedder,
    GraphStoreProvider,
    LLMInference,
    Reranker,
    VectorStoreProvider,
    _expand_depth,
    _expand_kinds,
)
from graphrag_proto.retrieval.context import (
    CONTEXT_TOKEN_LIMIT,
    ContextAssembly,
    estimate_tokens,
    render_skeleton,
)
from graphrag_proto.retrieval.ordering import DEFAULT_ORDERING, RRF_K_DEFAULT, resolve_ordering
from graphrag_proto.retrieval.profile import DomainProfileLoader, ProfileError
from graphrag_proto.retrieval.retrievers import GraphRetriever, VectorRetriever
from graphrag_proto.retrieval.semantic_cache import CachedAnswer, SemanticCache

Emit = Callable[[str, dict[str, Any]], None]

# Множитель оценки кандидата, которого фильтр НЕ подтвердил пересечением с запросом.
#
# ВАЖНО: это ЗАГЛУШКА, а не измеренная величина. Число зафиксировано ДО того, как оно
# было откалибровано, и обязано быть заменено результатом калибровки на наборе.
# Требование «понизить» из спецификации выполняется механизмом; насколько именно -
# решение, которое ещё не принято, и поэтому оно живёт в профиле, а не в коде.
FILTER_DEMOTE_SCALE = 0.5

# Что ось знает о документе-источнике (ADR-046, поправка владельца 2026-10-16). Три состояния,
# и они не сводятся к двум: `reachable_unused` — «узлы есть, обход не дошёл» — это не то же
# самое, что `blind` («нод нет»), хотя обе отвечают по вектору.
GRAPH_KNOWLEDGE_CONTRIBUTED = "contributed"   # документ пришёл обходом
GRAPH_KNOWLEDGE_REACHABLE = "reachable_unused"  # узлы в графе есть, обход не дошёл
GRAPH_KNOWLEDGE_BLIND = "blind"                # нод документа в графе нет
# Прибор не смотрел (ось выключена или проекция непроверяема). Отдельное значение, а не
# `blind`: «нод нет» и «не смотрели» — разные утверждения, и подмена первого вторым вернула бы
# ровно тот ложноотрицательный вывод, ради которого поправка вводится.
GRAPH_KNOWLEDGE_UNKNOWN = "unknown"

# Причины `reachable_unused`. Все три легитимны и дефектами не считаются: изолированная нода —
# допущение схемы (идти некуда), лимиты — конфигурация, отсутствие семян — свойство загрузки.
# Различать их надо, иначе три разных состояния схлопнутся в неразличимый ноль.
GRAPH_REASON_NO_CONTEXT_IDS = "no_context_ids"
GRAPH_REASON_NO_OUTGOING_EDGES = "no_outgoing_edges"
GRAPH_REASON_TRUNCATED_BY_LIMITS = "truncated_by_limits"
# Источник достигнут обходом, но кандидата не дал: обход принёс сущности, а читать
# нечего. Раньше такое состояние выражалось как `contributed`, то есть «принёс».
GRAPH_REASON_NO_CANDIDATE = "no_candidate"

#: Переключатель измерения: сколько строк графового свидетельства уходит в промпт.
#: ``None`` / пусто - без ограничения; ``0`` - свидетельство не показывается вовсе.
EVIDENCE_MAX_LINES_ENV = "RETRIEVAL_EVIDENCE_MAX_LINES"

_log = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = (
    "Ты — GraphRAG-ассистент. Отвечай строго по предоставленному контексту. "
    "Ссылайся на источники из контекста. Если контекста недостаточно — так и скажи."
)


def graph_search_enabled(profile: dict[str, Any]) -> bool:
    """Тумблер optional graph experiment: env > profile > disabled by default.

    `RETRIEVAL_GRAPH_ENABLED=false` всегда отключает experiment; без явного true baseline
    остаётся vector-only.
    """
    env = os.environ.get("RETRIEVAL_GRAPH_ENABLED")
    if env is not None and env.strip():
        return env.strip().lower() == "true"
    retrieval = profile.get("retrieval") or {}
    return bool(retrieval.get("graph_search_enabled", False))


def _config_number(
    section: Mapping[str, Any],
    key: str,
    default: float,
    fallbacks: builtins.list[str],
    *,
    path: str,
) -> float:
    """Читать числовой параметр конфигурации один раз, терпимо и С НАЗВАНИЕМ.

    Три свойства, каждое из которых раньше нарушалось:

    * **одно чтение.** `graph_boost` читался дважды: в теле trace-события (аргумент функции
      вычисляется до вызова, то есть даже при выключенном trace) и ниже по терпимой ветке.
      Первое чтение падало на негодном значении, и терпимая обработка была мёртвым кодом —
      «fallback есть» и «fallback работает» оказались разными утверждениями.
    * **терпимо.** Негодное значение в конфиге — дефект конфига, а не отказ системы. Раньше
      оно роняло всё расширение по графу, и вопрос выпадал из измерения целиком: опечатка в
      одной строке профиля стоила дороже, чем дефолт.
    * **с именем.** Тихий дефолт без следа — это «просто работает», а работает не то, что
      объявлено. Имя попадает в `config_fallbacks` ответа и дальше в артефакт прогона.
    """
    raw = section.get(key, default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        fallbacks.append(f"{path}.{key}")
        return float(default)


def _config_int(
    section: Mapping[str, Any],
    key: str,
    default: int,
    fallbacks: builtins.list[str],
    *,
    path: str,
) -> int:
    """То же для целого, но с проверкой диапазона: `max_depth: -1` — тоже негодное значение."""
    raw = section.get(key, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        fallbacks.append(f"{path}.{key}")
        return default
    if value < 0:
        fallbacks.append(f"{path}.{key}")
        return default
    return value


def _config_optional_int(
    section: Mapping[str, Any],
    key: str,
    fallbacks: builtins.list[str],
    *,
    path: str,
    ) -> int | None:
    """Необязательный целый параметр: отсутствие - это «не задан», а не ноль.

    Отдельная функция, потому что `_config_int` не различает «ключа нет» и «негодное
    значение»: обе ситуации дают дефолт. Здесь отсутствие ключа - это осмысленное
    «ограничения нет», и подменять его нулём нельзя: ноль означал бы «свидетельства не
    показывать никогда».
    """
    if key not in section or section.get(key) is None:
        return None
    raw = section[key]
    try:
        value = int(raw)
    except (TypeError, ValueError):
        fallbacks.append(f"{path}.{key}")
        return None
    if value < 0:
        fallbacks.append(f"{path}.{key}")
        return None
    return value


# Знаки, по которым видно, что в поле имени попала фраза, а не имя. Это не порог
# подбора, а проверка «похоже ли на имя вообще»: перечисление знаков не требует
# подбора, а длина потребовала бы произвольного порога, и такой порог был бы числом,
# выбранным без данных.
_PHRASE_MARKERS = (",", ";", ":", "—", "–", "?", "!", ". ")


def _looks_like_name(value: str) -> bool:
    """Отличает имя сущности от фразы, попавшей в то же поле.

    Причина проверки - измеренный дефект данных: `canonical_name` местами равен целой
    фразе. Такое значение обязано быть отражено в счётчике, а не молча участвовать в
    отборе: не имя не может ни подтвердить запрос, ни честно его опровергнуть.
    """
    text = str(value or "").strip()
    if not text:
        return False
    if any(marker in text for marker in _PHRASE_MARKERS):
        return False
    return len(text.split()) <= 3


def _name_variants(value: str) -> set[str]:
    """Формы имени, по которым оно ищется в тексте запроса.

    Учитываются и полная форма, и хвост после двоеточия: идентификаторы вида
    `Tag:Neo4j` и `entity:llm` приходят с префиксом, которого в вопросе нет.
    """
    text = str(value or "").strip().lower()
    variants = {text}
    for separator in (":", "/", "\\", "::"):
        if separator in text:
            tail = text.rsplit(separator, 1)[-1].strip()
            if tail:
                variants.add(tail)
    return {item for item in variants if item}


def _evidence_max_lines_from_env() -> int | None:
    """Верхняя граница числа строк графового свидетельства, `None` - без ограничения.

    Переключатель измерения, а не параметр домена: профиль версионируется вместе с
    корпусом, и добавление параметра туда означало бы перезалив корпуса ради одного
    сравнения. Поэтому здесь окружение, но применённое значение попадает в
    `effective_retrieval` и, значит, в артефакт прогона: значение, применённое к
    прогону, обязано быть видно, а не восстанавливаться из кода.

    `0` означает «свидетельство не показывать» - это и есть вторая рука измерения,
    где всё, кроме блока строк, остаётся прежним.
    """
    raw = os.environ.get(EVIDENCE_MAX_LINES_ENV)
    if raw is None or not raw.strip():
        return None
    try:
        value = int(raw.strip())
    except ValueError:
        _log.error(
            "%s=%r не целое число; ограничение не применено, свидетельство идёт как есть",
            EVIDENCE_MAX_LINES_ENV,
            raw,
        )
        return None
    if value < 0:
        _log.error(
            "%s=%r отрицательно; ограничение не применено, свидетельство идёт как есть",
            EVIDENCE_MAX_LINES_ENV,
            raw,
        )
        return None
    return value


def profile_fingerprint(profile: Mapping[str, Any]) -> str:
    """Идентичность конфигурации: короткий отпечаток по канонической сериализации.

    Третья нога в артефакте измерения рядом с `code_commit` и `revision`. Без неё отчёт не
    может сказать, каким конфигом измерен, а восстановить его из версии кода нельзя: код
    менялся после прогона, и параметры конфигурации в коммите не закреплены.
    """
    canonical = json.dumps(profile, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def _request_depth(value: int | None) -> int | None:
    """Глубина обхода, заданная запросом. `None` - «не задана», и тогда решает конфигурация.

    Проверяются тип и знак, но НЕ верхняя граница. Просьба «дальше, чем готов обойти» -
    это не дефект запроса, а объявление о намерении, поэтому она зажимается и
    объявляется (`depth_clamped` в ответе и в трассировке), а не отвергается. Потолок
    принадлежит системе и живёт в `MAX_EXPANSION_DEPTH`, а не в проверке запроса.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("max_depth должен быть целым числом")
    if value < 1:
        raise ValueError("max_depth должен быть не меньше 1")
    return value


def _validate_runtime_profile(profile: dict[str, Any]) -> None:
    if not isinstance(profile, dict):
        raise ProfileError("Domain Profile должен быть mapping")


def _noop_emit(event_type: str, payload: dict[str, Any]) -> None:
    return None


def build_sources(
    body_chunks: list[dict[str, Any]],
    skeleton_rows: list[dict[str, Any]] | None = None,
    *,
    graph_knowledge: Mapping[str, str] | None = None,
    graph_knowledge_reasons: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Уникальные источники с максимальным relevance по паре (source_url, axis).

    Ось `vector` — из body-чанков, ось `graph` — из узлов графового скелета
    (design.md §2, аддитивное поле `axis`, ADR-016). `retrieval_metrics`
    по-прежнему работает по `source_url`.

    `graph_knowledge` — по `source_url`: `contributed` / `reachable_unused` / `blind`
    (ADR-046, поправка владельца 2026-10-16). Без этой метки «граф не помог» и «граф про этот
    документ ничего не знает» неразличимы, а на частично наполненном корпусе это разные
    результаты. Причина для `reachable_unused` приходит отдельной картой: три её состояния
    легитимны, но различать их надо, иначе они снова станут одним нулём.

`relevance` — свойство источника (ADR-064). Если у источника есть кандидат, значение равно
    оценке этого кандидата и одинаково для обеих его осей; если кандидата нет, значение `None` с
    basis `none`, потому что оценивать нечего. Раньше здесь стояла константа `1.0` для каждой
    оси графа, и рядом с оценкой кандидата она читалась как сравнение, которого никто не делал.
    Ни одна метрика это поле не читает: `retrieval_metrics` считает по порядку источников.
    """
    best: dict[tuple[str, str], float] = {}
    linked: dict[str, int] = {}
    scored: dict[str, float] = {}
    for chunk in body_chunks:
        source = str(chunk.get("source_url") or "")
        if not source:
            continue
        score = float(chunk.get("score") or 0.0)
        key = (source, "vector")
        best[key] = max(best.get(key, 0.0), score)
        scored[source] = max(scored.get(source, 0.0), score)
        if chunk.get("context_ids") or chunk.get("tag_ids"):
            linked[source] = linked.get(source, 0) + 1
    for row in skeleton_rows or []:
        for url in _skeleton_source_urls(row):
            # Ключ по-прежнему различает ось (источник может быть принесён обеими), но
            # значение больше не константа: ниже оно берётся из `scored` либо остаётся None.
            best.setdefault((url, "graph"), 0.0)
    knowledge = graph_knowledge if graph_knowledge is not None else {}
    reasons = graph_knowledge_reasons or {}
    # Порядок источников - по лучшей оценке кандидата, а не по оси. Прежний ключ
    # `(0 если ось graph, ...)` был вторым правилом порядка рядом с оценкой: он ставил
    # всё, что нашёл обход, выше векторных ДО того, как оценки вообще были посчитаны.
    # Источник без кандидата идёт после: у него нет оценки, и ставить его по константе
    # значит выдумать оценку.
    ordered = sorted(
        best.items(),
        key=lambda pair: (
            0 if pair[0][0] in scored else 1,
            -scored.get(pair[0][0], 0.0),
            pair[0][0],
        ),
    )
    # `graph_knowledge is None` — карту не вычисляли (ось выключена или проекция
    # непроверяема). Метка по умолчанию тогда `unknown`, а не `blind`: прибор не смотрел, и
    # объявлять «нод нет» он не вправе.
    default_knowledge = (
        GRAPH_KNOWLEDGE_BLIND if graph_knowledge is not None else GRAPH_KNOWLEDGE_UNKNOWN
    )
    # Метка принадлежит ИСТОЧНИКУ, а не оси (ADR-064). Раньше запись оси графа всегда
    # получала `contributed`, из-за чего один и тот же источник мог нести в двух записях
    # противоречащие друг другу метки: «граф принёс» и «граф не донёс».
    #
    # Источник, которого достиг обход, но который не дал кандидата, — это ровно
    # `reachable_unused`, и раньше это состояние нельзя было выразить вовсе. Выводится
    # здесь, а не из карты знания: «обход дошёл» видно по строкам свидетельства, и для
    # этого карта не нужна. Карта нужна только чтобы отличить `blind` от `unknown`.
    reached_by_walk = {
        url
        for row in skeleton_rows or []
        for url in _skeleton_source_urls(row)
    }
    rows: list[dict[str, Any]] = []
    for (source, axis), _score in ordered:
        if source in knowledge:
            label = knowledge[source]
        elif source in reached_by_walk:
            label = GRAPH_KNOWLEDGE_REACHABLE
        else:
            label = default_knowledge
        rows.append(
            {
                "source_url": source,
                # `None`, а не 0.0: кандидата не было, оценку не считали. Ноль читался бы как
                # «оценили и признали неважным».
                "relevance": round(scored[source], 4) if source in scored else None,
                "relevance_basis": "score" if source in scored else "none",
                "axis": axis,
                "graph_knowledge": label,
                "graph_knowledge_reason": reasons.get(source)
                or (
                    GRAPH_REASON_NO_CANDIDATE
                    if source in reached_by_walk and source not in scored
                    else None
                ),
                "graph_linked_chunks": int(linked.get(source, 0)) if axis == "vector" else 0,
            }
        )
    return rows


def _graph_knowledge_for_sources(
    *,
    body_chunks: list[dict[str, Any]],
    skeleton_rows: list[dict[str, Any]] | None,
    graph_known_sources: set[str],
    expanded_rows: list[dict[str, Any]],
) -> tuple[dict[str, str], dict[str, str]]:
    """Метка «что ось знает» по документу и причина для `reachable_unused`.

    Вызывается только когда карта вычислена; «не считали» обрабатывает вызывающий, и
    различение живёт в `None`, а не в пустом словаре.

    Причины различаются по трём наблюдаемым признакам, и все три состояния легитимны:
    семян не было — идти было не от чего; семены были, а обход пуст — из узлов нет исходящих
    рёбер; обход отработал и вернул строки, но документа в них нет — документ не попал в
    лимиты глубины/фанута/узлов.
    """
    reached = {
        url
        for row in skeleton_rows or []
        for url in _skeleton_source_urls(row)
    }
    knowledge: dict[str, str] = {}
    reasons: dict[str, str] = {}
    for source in {str(chunk.get("source_url") or "") for chunk in body_chunks} - {""}:
        if source in reached:
            # Документ пришёл и по вектору, и обходом — это две записи об одном факте, и
            # векторная не должна выглядеть «слепой»: обход до документа дошёл. Ось поля
            # уже говорит, каким слотом запись пришла; метка отвечает на вопрос о документе.
            knowledge[source] = GRAPH_KNOWLEDGE_CONTRIBUTED
            continue
        if source not in graph_known_sources:
            knowledge[source] = GRAPH_KNOWLEDGE_BLIND
            continue
        knowledge[source] = GRAPH_KNOWLEDGE_REACHABLE
        seeded = any(
            chunk.get("context_ids") or chunk.get("tag_ids")
            for chunk in body_chunks
            if str(chunk.get("source_url") or "") == source
        )
        if not seeded:
            reasons[source] = GRAPH_REASON_NO_CONTEXT_IDS
        elif not expanded_rows:
            reasons[source] = GRAPH_REASON_NO_OUTGOING_EDGES
        else:
            reasons[source] = GRAPH_REASON_TRUNCATED_BY_LIMITS
    return knowledge, reasons


def _skeleton_source_urls(row: dict[str, Any]) -> list[str]:
    """source_url'ы узлов скелета: у Source/Chunk — `source_url`, у канонических узлов — `source_ids`."""
    urls: list[str] = []
    for node in (row.get("n") or {}, row.get("m") or {}):
        if not isinstance(node, dict):
            continue
        value = node.get("source_url") or node.get("source_ids")
        if isinstance(value, str):
            if value:
                urls.append(value)
        elif isinstance(value, list):
            urls.extend(str(u) for u in value if str(u))
    return urls


@dataclass(frozen=True)
class ProjectionReadiness:
    """Пораздельная готовность домена (ADR-046 п. 3 и п. 6).

    **Каждое поле — `int | None`, и `None` значит «не измерено», а не «ноль».** Разница между
    «расхождений нет» и «измерять было нечем» — ровно та ошибка, из-за которой проект уже
    получал `necessity = 0` вместо результата: уверенный ноль там, где данных нет.

    `sources_active` здесь **нет намеренно**: активные источники знает реестр, а query-контур
    реестра не видит — он получает только ревизии, опубликованные ингестом. Называть это
    «активными источниками» было бы подменой единицы; см. ADR-046 п. 6.
    """

    sources_tracked: int
    sources_without_graph: int | None
    revision_mismatches: int | None
    chunks_without_owner: int | None
    chunks_total: int | None
    reason: str

    @property
    def ready(self) -> bool:
        """Готовность к обслуживанию, а не полнота корпуса.

        `sources_without_graph` условием **не является**: документ, нод которого в графе нет,
        обслуживается вектором, и это объявляется меткой `blind` у источника, а не отказом всей
        оси (ADR-046, поправка владельца 2026-10-16). Счётчик остаётся в `projection_counters`,
        то есть масштаб слепоты читатель видит.

        Два оставшихся условия — настоящая рассинхронизация: чанк несёт ревизию не своего
        источника либо у него нет владельца. Это не «документ ещё не загружен», и отучать гард
        от них нельзя.
        """
        return (
            self.sources_tracked > 0
            and self.revision_mismatches == 0
            and self.chunks_without_owner == 0
        )


def evaluate_projection_readiness(
    expected: dict[str, str],
    observed: dict[str, dict[str, int]] | None,
) -> ProjectionReadiness:
    """Считает готовность пораздельно: журнал заявленных ревизий против ревизий в графе.

    `observed is None` — адаптер не умеет отдавать данные, и это `unknown`, а не «всё хорошо».
    """
    if observed is None:
        return ProjectionReadiness(
            sources_tracked=len(expected),
            sources_without_graph=None,
            revision_mismatches=None,
            chunks_without_owner=None,
            chunks_total=None,
            reason="projection_not_measurable",
        )
    chunks_total = sum(sum(revisions.values()) for revisions in observed.values())
    if not expected:
        return ProjectionReadiness(
            sources_tracked=0,
            sources_without_graph=None,
            revision_mismatches=None,
            chunks_without_owner=chunks_total if chunks_total else None,
            chunks_total=chunks_total,
            reason="no_tracked_sources",
        )
    mismatches = 0
    for owner, revisions in observed.items():
        want = expected.get(owner)
        if want is None:
            # Источник не опубликован ингестом: это «нет заявленной ревизии», а не «ревизия
            # не та», и дефект другой. Считается в `chunks_without_owner`, иначе один и тот
            # же чанк попал бы в два счётчика и сумма перестала бы что-то значить.
            continue
        mismatches += sum(count for revision, count in revisions.items() if revision != want)
    without_owner = sum(
        sum(revisions.values())
        for owner, revisions in observed.items()
        if owner == OWNERLESS_SOURCE or owner not in expected
    )
    without_graph = sum(1 for owner in expected if owner not in observed)
    return ProjectionReadiness(
        sources_tracked=len(expected),
        sources_without_graph=without_graph,
        revision_mismatches=mismatches,
        chunks_without_owner=without_owner,
        chunks_total=chunks_total,
        reason="checked",
    )


def _counters_payload(readiness: ProjectionReadiness | None) -> dict[str, Any] | None:
    """Счётчики готовности в отчёт прогона (ADR-046 п. 6: «в состояние и в отчёт»).

    `None` превращается в `null`, а не в `0`: в JSON отчёта «не измерено» и «ноль»
    обязаны различаться, иначе агрегатор отчётов сложит их вместе.
    """
    if readiness is None:
        return None
    return {
        "sources_tracked": readiness.sources_tracked,
        "sources_without_graph": readiness.sources_without_graph,
        "revision_mismatches": readiness.revision_mismatches,
        "chunks_without_owner": readiness.chunks_without_owner,
        "chunks_total": readiness.chunks_total,
        "reason": readiness.reason,
    }


class QueryPipeline:
    def __init__(
        self,
        embedder: Embedder,
        graph_store: GraphStoreProvider | None,
        vector_store: VectorStoreProvider,
        reranker: Reranker,
        llm: LLMInference,
        profile_loader: DomainProfileLoader | None = None,
        max_graph_nodes: int = 5,
        max_vector_chunks: int = 5,
        executor: ThreadPoolExecutor | None = None,
        semantic_cache: SemanticCache | None = None,
        strict_profile: bool = False,
        projection_state_store: ProjectionStateStore | None = None,
        projection_config_fingerprint: str | None = None,
        projection_state_required: bool = False,
        projection_metrics: ProjectionMetrics | None = None,
    ) -> None:
        self._embedder = embedder
        self._graph_store = graph_store
        self._vector_store = vector_store
        self._reranker = reranker
        self._llm = llm
        self._profiles = profile_loader or DomainProfileLoader()
        self._strict_profile = strict_profile
        self._projection_state_store = projection_state_store
        self._projection_config_fingerprint = projection_config_fingerprint
        self._projection_state_required = projection_state_required
        self.projection_metrics = projection_metrics or ProjectionMetrics()
        self._max_graph_nodes = max_graph_nodes
        self._max_vector_chunks = max_vector_chunks
        self._executor = executor or ThreadPoolExecutor(max_workers=2, thread_name_prefix="query-pipeline")
        # Semantic Cache (бандл 3/3): None — выключено (поведение M2/M3, backward-compat).
        self._semantic_cache = semantic_cache
        self._cache_threshold = semantic_cache.threshold if semantic_cache is not None else 0.0
        self._closed = False
        # Конфигурация закрепляется на первый запрос и дальше неизменна в рамках сессии.
        # Поля не `_cache_*`: это не ускоритель, а момент применения, и называть его
        # кэшем значило бы приучить читателя думать, что значение может устареть.
        self._pinned_profile: dict[str, Any] | None = None
        self._pinned_fingerprint: str | None = None
        self._pinned_at: str | None = None

    def shutdown(self) -> None:
        """Закрытие общего executor'а (вызывается при замене пайплайна/остановке воркера)."""
        self._closed = True
        self._executor.shutdown(wait=False)

    def active_domain(self) -> str:
        return self._profiles.active_domain()

    def run(
        self,
        query: str,
        domain: str | None = None,
        emit: Emit | None = None,
        revision: str | None = None,
        generate: bool = True,
        trace: bool = False,
        max_depth: int | None = None,
    ) -> dict[str, Any]:
        """Полный retrieval-цикл; `revision` — ревизия данных домена (ADR-026).

        `max_depth` — глубина обхода, заданная запросом (ADR-036). Она ВЫШЕ конфигурации:
        запрос -> Config Service -> профиль -> кодовый фолбэк. Значение проверяется типом и
        знаком; всё, что выше `MAX_EXPANSION_DEPTH`, зажимается и объявляется в ответе
        (`max_depth_effective`, `depth_clamped`), чтобы урезание не было молчаливым.
        `None` оставляет цепочку конфигурации нетронутой.

        Ревизия участвует в epoch-bump кэша и попадает в `done.revision` как
        fingerprint среза для Eval (ADR-015). Hit эпохи `revision` означает, что
        ответ собран по данным именно этой ревизии — одна ревизия в одном ответе.

        `generate=False` — retrieval-only (design.md §6): Context Assembly есть,
        LLM не вызывается, `text=""` и `generation_time_s=0`.

        `trace=True` — диагностический слой 4 (design.md §5.3): в `done["trace"]`
        кладётся список событий этапов; метрики от трассы не зависят и слои 1–3
        пишутся независимо (без `trace` поле отсутствует — контракт не меняется).
        """
        emit = emit or _noop_emit
        if self._closed:
            raise RuntimeError("QueryPipeline закрыт")
        # Проверяем ДО эмбеддинга: негодная глубина не должна стоить обращения к модели.
        request_depth = _request_depth(max_depth)
        total_started = time.monotonic()
        trace_events: list[dict[str, Any]] = []

        def _trace(payload: dict[str, Any]) -> None:
            if trace:
                trace_events.append(payload)

        emit("status", {"stage": "embedding"})
        active = domain or self._profiles.active_domain()
        embed_started = time.monotonic()
        embedding = self._embedder.embed(query, active)
        _trace(
            {
                "stage": "embedding",
                "ms": round((time.monotonic() - embed_started) * 1000, 1),
                "dimensions": len(embedding),
            }
        )

        profile = self._load_profile(active)
        graph_requested = graph_search_enabled(profile)
        limit = _context_limit(profile)
        enabled = graph_requested
        graph_degraded = False
        # Параметры запроса читаются ОДИН раз, до расширения, и читаются терпимо.
        # Разделение принципиальное: дефект конфигурации — это «работаем с дефолтом и
        # называем его», а отказ рантайма (Neo4j недоступен) — это деградация. Раньше
        # негодное значение в профиле попадало в широкий `except` расширения и обнуляло
        # вклад графа целиком, то есть дефект конфига выглядел как отказ системы.
        config_fallbacks: builtins.list[str] = []
        retrieval_cfg: dict[str, Any] = profile.get("retrieval") or {}
        cfg_max_depth = _config_int(retrieval_cfg, "max_depth", 2, config_fallbacks, path="retrieval")
        cfg_max_fanout = _config_int(retrieval_cfg, "max_fanout", 8, config_fallbacks, path="retrieval")
        cfg_max_graph_nodes = _config_int(
            retrieval_cfg, "max_graph_nodes", self._max_graph_nodes, config_fallbacks, path="retrieval"
        )
        # Масштаб понижения фильтра. ЧИТАЕТСЯ ИЗ ПРОФИЛЯ и применяется как есть: величина
        # понижения - это решение, а не деталь реализации, и выбирать её в коде значило
        # бы зашить число, у которого нет ни данных, ни владельца.
        cfg_filter_scale = _config_number(
            retrieval_cfg, "filter_demote_scale", FILTER_DEMOTE_SCALE, config_fallbacks,
            path="retrieval",
        )
        if not 0.0 <= cfg_filter_scale <= 1.0:
            # Вне (0, 1] понижение перестаёт быть понижением и становится подъёмом -
            # ровно тот старый вред, который убирали.
            config_fallbacks.append("retrieval.filter_demote_scale")
            _log.error(
                "retrieval.filter_demote_scale вне (0, 1]: %s, применён %s",
                cfg_filter_scale,
                FILTER_DEMOTE_SCALE,
            )
            cfg_filter_scale = FILTER_DEMOTE_SCALE
        # Имя реализации порядка. Читается здесь, разрешается позже: момент чтения и
        # момент применения - разные вещи, и оба попадают в артефакт.
        cfg_ordering = str(retrieval_cfg.get("ordering") or DEFAULT_ORDERING).strip()
        cfg_rrf_k = int(
            _config_number(retrieval_cfg, "rrf_k", RRF_K_DEFAULT, config_fallbacks, path="retrieval")
        )
        expansion_kinds_raw = retrieval_cfg.get("expansion_kinds")
        cfg_expansion_kinds = (
            [str(item) for item in expansion_kinds_raw] if isinstance(expansion_kinds_raw, list) else None
        )
        if expansion_kinds_raw is not None and cfg_expansion_kinds is None:
            config_fallbacks.append("retrieval.expansion_kinds")
        cfg_direction = str(retrieval_cfg.get("expansion_direction", "both"))
        # Ограничение числа строк свидетельства: окружение важнее профиля. Отделено от
        # `max_graph_nodes` НАМЕРЕННО - тот ограничивает обход, этот ограничивает только
        # то, что попадёт в промпт. Иначе невозможно снять блок, не сломав атрибуцию
        # источников, а это разные вещи.
        cfg_evidence_max_lines = _evidence_max_lines_from_env()
        if cfg_evidence_max_lines is None:
            cfg_evidence_max_lines = _config_optional_int(
                retrieval_cfg, "evidence_max_lines", config_fallbacks, path="retrieval"
            )
        # Считается один раз и здесь: и отчёт, и трассировка, и сам обход обязаны говорить
        # об одном и том же числе, иначе «что показал отчёт» и «что сделал обход» разъедутся.
        requested_depth = cfg_max_depth if request_depth is None else request_depth
        effective_depth = _expand_depth(requested_depth)
        effective_retrieval: dict[str, Any] = {
            "max_depth": cfg_max_depth,
            "max_depth_requested": requested_depth,
            "max_depth_effective": effective_depth,
            "depth_clamped": effective_depth != requested_depth,
            "max_fanout": cfg_max_fanout,
            "max_graph_nodes": cfg_max_graph_nodes,
            "expansion_kinds": cfg_expansion_kinds,
            "expansion_direction": cfg_direction,
            "graph_search_enabled": graph_requested,
            "evidence_max_lines": cfg_evidence_max_lines,
            "context_limit": limit,
        }
        # Имя реализации порядка пишется в тот же словарь, но ПОСЛЕ разрешения: пока
        # имя только запрошено, записать «применённое» было бы выдумкой. Дописывается
        # внизу, рядом с местом, где реализация реально отработала.
        effective_retrieval["ordering_requested"] = cfg_ordering
        effective_retrieval["ordering_applied"] = None
        effective_retrieval["ordering_params"] = None
        effective_retrieval["ordering_fallback"] = False
        projection_status = "not_requested" if not graph_requested else "not_tracked"
        projection_revision: str | None = None
        projection_state: ProjectionState | None = None
        projection_counters: ProjectionReadiness | None = None
        # Документы, чьи строки есть в графе. Пусто не значит «граф ничего не знает»: при
        # выключенной оси или непроверяемой проекции карта не вычислялась, и это обязано
        # отличаться от вычисленного пустого множества — иначе «не измерено» станет нулём.
        graph_known_sources: set[str] | None = None
        # Семена обхода. Объявляется здесь, а не внутри ветки `if enabled`: метка источника
        # читается при любом состоянии оси, и ссылка на неё из `build_sources` не должна
        # зависеть от того, открылась ось или нет.
        unique_seed_ids: list[str] = []
        if graph_requested and self._projection_state_store is None and self._projection_state_required:
            enabled = False
            graph_degraded = True
            projection_status = "state_store_unavailable"
        elif graph_requested and self._projection_state_store is not None:
            try:
                projection_state = self._projection_state_store.get(active)
            except Exception:  # noqa: BLE001
                enabled = False
                graph_degraded = True
                projection_status = "state_store_error"
            else:
                config_fingerprint = self._projection_config_fingerprint or "default"
                if projection_state is None:
                    enabled = False
                    graph_degraded = True
                    projection_status = "missing"
                elif revision is None:
                    enabled = False
                    graph_degraded = True
                    projection_status = "revision_unknown"
                elif projection_state.config_fingerprint != config_fingerprint:
                    enabled = False
                    graph_degraded = True
                    projection_status = "config_mismatch"
                elif not projection_state.is_ready(revision, config_fingerprint):
                    enabled = False
                    graph_degraded = True
                    projection_status = projection_state.status
                elif self._graph_store is None:
                    enabled = False
                    graph_degraded = True
                    projection_status = "adapter_missing"
                else:
                    # ADR-046 п. 3 и п. 8: доменная ревизия больше не решает готовность —
                    # она не может ответить на вопрос «изменились ли данные этого источника».
                    # Проверка идёт пораздельная: журнал опубликованных ревизий против
                    # ревизий, которые несут чанки, и всё это за один проход по графу.
                    try:
                        expected = self._projection_state_store.source_revisions(active)
                        observed = self._graph_store.chunk_revisions_by_source(active)
                    except Exception:  # noqa: BLE001
                        expected, observed = {}, None
                    readiness = evaluate_projection_readiness(expected, observed)
                    projection_counters = readiness
                    if not readiness.ready:
                        enabled = False
                        graph_degraded = True
                        projection_status = (
                            readiness.reason
                            if readiness.reason != "checked"
                            else (
                                "sources_without_graph"
                                if readiness.sources_without_graph
                                else "revision_mismatch"
                                if readiness.revision_mismatches
                                else "chunks_without_owner"
                            )
                        )
                    else:
                        projection_status = "ready"
                        projection_revision = projection_state.projection_revision
                        # Что граф знает о документах домена — для метки у источника. При
                        # `observed is None` карта пуста и все источники получают `blind`:
                        # прибор не измерил, что знает граф, и не должен изображать знание.
                        graph_known_sources = (
                            set(observed) if isinstance(observed, dict) else set()
                        )
        if enabled and self._graph_store is None:
            enabled = False
            graph_degraded = True
            projection_status = "adapter_missing"
        if graph_requested and not enabled:
            _trace(
                {
                    "stage": "graph_readiness",
                    "degraded": True,
                    "status": projection_status,
                    "projection_revision": (
                        projection_state.projection_revision if projection_state is not None else None
                    ),
                    # Счётчики идут в трассу даже когда всё хорошо: «нулей нет» и «измерять
                    # было нечем» выглядят в логе одинаково, если не показать оба.
                    "projection_counters": _counters_payload(projection_counters),
                }
            )
        elif projection_counters is not None:
            _trace(
                {
                    "stage": "graph_readiness",
                    "degraded": False,
                    "status": projection_status,
                    "projection_counters": _counters_payload(projection_counters),
                }
            )
        graph_cache_ready = (
            graph_requested
            and enabled
            and projection_status == "ready"
            and projection_revision is not None
        )
        cache_allowed = True
        cache_revision = (
            f"{revision or 'default'}:{'graph' if graph_cache_ready else 'vector'}:"
            f"{projection_revision if graph_cache_ready else 'none'}"
        )

        # Semantic Cache (бандл 3/3): hit — до store/LLM, без token-событий и обращений к осям.
        cache_lookup_s = 0.0
        if self._semantic_cache is not None and cache_allowed:
            cache_started = time.monotonic()
            cached = self._semantic_cache.lookup(
                embedding, self._cache_threshold, active, revision=cache_revision
            )
            cache_lookup_s = round(time.monotonic() - cache_started, 3)
            if cached is not None:
                emit("status", {"stage": "cache", "hit": True})
                _trace({"stage": "cache", "hit": True})
                cached_done: dict[str, Any] = {
                    "text": cached.text,
                    "sources": cached.sources,
                    "cache_hit": True,
                    "cache_lookup_s": cache_lookup_s,
                    "generation_time_s": 0.0,
                    "retrieval_time_s": 0.0,
                    "total_time_s": round(time.monotonic() - total_started, 3),
                    "revision": revision,
                    "graph_degraded": graph_degraded,
                    "projection_status": projection_status,
                    "projection_revision": projection_revision,
                    # Сборка контекста не выполнялась: кэш отсюда не читался. None, а не
                    # нули - см. ветку ниже без кэша.
                    "context": None,
                }
                if graph_requested and graph_degraded:
                    self.projection_metrics.observe_fallback(
                        projection_status if projection_status != "ready" else "graph_expansion"
                    )
                _trace({"stage": "done", "total_time_s": cached_done["total_time_s"]})
                if trace:
                    cached_done["trace"] = trace_events
                emit("done", cached_done)
                return cached_done

        graph_retriever = (
            GraphRetriever(
                self._graph_store,
                profile,
                max_nodes=self._max_graph_nodes,
                domain=active,
            )
            if self._graph_store is not None
            else None
        )
        vector_retriever = VectorRetriever(
            self._vector_store,
            top_k=self._max_vector_chunks,
            domain=active,
        )

        retrieval_started = time.monotonic()
        emit("status", {"stage": "vector"})
        body_chunks = vector_retriever.retrieve(embedding)
        vector_scores = {
            str(chunk.get("chunk_id")): float(chunk.get("score") or 0.0)
            for chunk in body_chunks
        }
        skeleton_rows: list[dict[str, Any]] = []
        expanded_rows: list[dict[str, Any]] = []
        # Мост объявляется здесь, а заполняется ниже: `effective_retrieval` собирается
        # раньше обхода, и итог моста дописывается в него после.
        bridge_status = "not_requested"
        bridged_chunks: list[dict[str, Any]] = []
        if enabled:
            emit("status", {"stage": "graph", "enabled": True})
            seed_ids = [
                str(value)
                for chunk in body_chunks
                for key in ("context_ids", "tag_ids")
                for value in (chunk.get(key) or [])
                if value
            ]
            unique_seed_ids = list(dict.fromkeys(seed_ids))
            if graph_retriever is None:
                graph_degraded = True
                _trace({"stage": "graph_expansion", "degraded": True, "reason": "adapter_missing"})
            elif unique_seed_ids:
                try:
                    # Здесь только работа с графом: отказы рантайма деградируют, дефекты
                    # конфигузации разобраны выше и сюда не доходят.
                    expansion_kinds = cfg_expansion_kinds
                    expanded_rows = graph_retriever.expand(
                        unique_seed_ids,
                        direction=cfg_direction,
                        kinds=expansion_kinds,
                        max_depth=requested_depth,
                        max_fanout=cfg_max_fanout,
                        max_nodes=cfg_max_graph_nodes,
                    )
                    if not expanded_rows:
                        graph_degraded = True
                        _trace(
                            {
                                "stage": "graph_expansion",
                                "seed_chunk_ids": [chunk.get("chunk_id") for chunk in body_chunks],
                                "context_ids": unique_seed_ids,
                                "paths": [],
                                "degraded": True,
                                "reason": (
                                    "no_matching_edge_kinds"
                                    if _expand_kinds(expansion_kinds) is not None
                                    else "empty_projection"
                                ),
                            }
                        )
                    else:
                        skeleton_rows = [
                            {"n": row, "m": None, "rel_type": row.get("kind", "RELATED")}
                            for row in expanded_rows
                        ]
                        _trace(
                            {
                                "stage": "graph_expansion",
                                "seed_chunk_ids": [chunk.get("chunk_id") for chunk in body_chunks],
                                "context_ids": unique_seed_ids,
                                "paths": [row.get("path", []) for row in expanded_rows],
                                "depths": [row.get("depth", 0) for row in expanded_rows],
                                "origins": [row.get("origin") for row in expanded_rows],
                                "confidences": [row.get("confidence") for row in expanded_rows],
                                 # Уже прочитанное и уже терпимо нормализованное значение, а не
                                 # второе чтение того же поля. Второе чтение здесь означало бы
                                 # `float(...)` на негодном значении, падало ВНУТРИ этого
                                 # `try` и делало именованный fallback ниже недостижимым.
                                 "requested_depth": requested_depth,
                                "effective_depth": effective_depth,
                                "depth_clamped": effective_depth != requested_depth,
                            }
                        )
                except Exception:  # noqa: BLE001
                    graph_degraded = True
                    _trace({"stage": "graph_expansion", "degraded": True})
            else:
                graph_degraded = True
                _trace(
                    {
                        "stage": "graph_expansion",
                        "context_ids": [],
                        "paths": [],
                        "degraded": True,
                        "reason": "no_context_ids",
                    }
                )

        # МОСТ «обход → читаемый кандидат». Обход отдаёт `chunk_ids` достигнутых сущностей,
        # но не текст; без этого шага графовый кандидат остаётся именем.
        #
        # Оценки bridged-кандидатам НЕ выдаётся. Раньше документы, достигнутые обходом,
        # получали константу и вставали выше векторных — это стоило нам -0.0417 recall@5.
        # Выдумывать вторую константу значило бы повторить то же с другой стороны: порядок
        # здесь должен задавать плагин смешения, а не этот шаг.
        if enabled and expanded_rows:
            requested_ids = [
                str(value)
                for row in expanded_rows
                for value in (row.get("chunk_ids") or [])
                if value
            ]
            unique_requested = list(dict.fromkeys(requested_ids))
            if unique_requested and self._vector_store is None:
                bridge_status = "adapter_missing"
            elif unique_requested:
                try:
                    fetched = self._vector_store.get_chunks_by_ids(unique_requested, domain=active)
                except Exception as exc:  # noqa: BLE001 - деградация, а не отказ запроса
                    bridge_status = "error"
                    _trace({"stage": "bridge", "status": "error", "error": str(exc)})
                else:
                    if fetched is None:
                        # Хранилище не умеет читать по идентификаторам. Это НЕ «чанков нет»
                        # и не «граф ничего не дал»: отсутствие возможности обязано быть
                        # видно, иначе пропадает различие между тремя разными причинами.
                        bridge_status = "unsupported"
                    else:
                        bridge_status = "ok"
                        already = {str(chunk.get("chunk_id")) for chunk in body_chunks}
                        for row in fetched:
                            chunk_id = str(row.get("chunk_id") or "")
                            if not chunk_id or chunk_id in already:
                                continue
                            already.add(chunk_id)
                            bridged_chunks.append(
                                {
                                    "chunk_id": chunk_id,
                                    "text": str(row.get("text") or ""),
                                    "source_url": row.get("source_url"),
                                    "domain": row.get("domain"),
                                    "index": row.get("index"),
                                    "bridged": True,
                                }
                            )
                        body_chunks = body_chunks + bridged_chunks

        # Итог моста дописывается в `effective_retrieval` ЗДЕСЬ: сам словарь собирается
        # раньше обхода, и записать в него значение можно только после того, как оно
        # известно. Писать его в литерал означало бы зафиксировать «not_requested».
        effective_retrieval["bridge_status"] = bridge_status
        effective_retrieval["bridged_chunks"] = len(bridged_chunks)

        # ФИЛЬТР: мягкий, по пересечению с запросом. Понижает оценку, не выбрасывает.
        #
        # Пересечение считается по тем же строкам обхода, которые построил обход: имя
        # достигнутой сущности ищется в тексте запроса, и чанки подтверждённых сущностей
        # пересекаются с запросом. Отдельный порт хранилища не нужен и в Neo4j, и в
        # InMemory - один и тот же вход для обоих, а значит и один смысл.
        #
        # Пустой набор подтверждённых сущностей - это НЕ «всё не подтверждено»: это
        # отсутствие данных, и в этом состоянии фильтр не трогает ничего. Иначе при
        # выключенном графе он понизил бы весь выдачный набор и выглядел бы как
        # отрицательный результат механизма.
        filter_status = "not_requested"
        filter_demoted: list[dict[str, Any]] = []
        filter_phrase_names = 0
        corroborated_chunk_ids: set[str] = set()
        if enabled and expanded_rows and body_chunks:
            query_text = str(query or "").lower()
            for row in expanded_rows:
                raw_name = str(row.get("canonical_name") or "")
                if not _looks_like_name(raw_name):
                    filter_phrase_names += 1
                    continue
                variants = _name_variants(raw_name)
                if not any(variant in query_text for variant in variants):
                    continue
                corroborated_chunk_ids.update(
                    str(value) for value in (row.get("chunk_ids") or []) if value
                )
            if not corroborated_chunk_ids:
                # Ни одна достигнутая сущность не подтверждена запросом: данных для
                # отбора нет, и отбор не производится.
                filter_status = "no_corroborated_entities"
            else:
                filter_status = "applied"
                for chunk in body_chunks:
                    if str(chunk.get("chunk_id") or "") in corroborated_chunk_ids:
                        continue
                    chunk["filter_demoted"] = True
                    chunk["score"] = float(chunk.get("score") or 0.0) * cfg_filter_scale
                    filter_demoted.append(
                        {
                            "chunk_id": str(chunk.get("chunk_id") or ""),
                            "source_url": chunk.get("source_url"),
                            "bridged": bool(chunk.get("bridged")),
                        }
                    )
                # Ранжирование ниже заменит оценку своей, поэтому понижение видно только
                # здесь. Это сказано прямо, иначе потеря понижения выглядела бы как
                # «фильтр не сработал».
                effective_retrieval["filter_applied_stage"] = "pre_order"
        elif enabled and body_chunks:
            filter_status = "no_expansion_rows"

        effective_retrieval["filter_status"] = filter_status
        effective_retrieval["filter_demote_scale"] = cfg_filter_scale
        # Статус величины едет вместе с числом. Пока откалибровать нечем, а число в артефакте
        # читалось бы как решение; со статусом оно читается как то, чем является.
        # Отличать заглушку от измеренного значения обязано само значение, а не этот комментарий.
        effective_retrieval["filter_demote_scale_status"] = (
            "profile" if cfg_filter_scale != FILTER_DEMOTE_SCALE else "uncalibrated_placeholder"
        )
        effective_retrieval["filter_demoted"] = len(filter_demoted)
        effective_retrieval["filter_demoted_candidates"] = filter_demoted
        # Отдельно от `filter_demoted`: это счётчик качества данных, а не отбор.
        effective_retrieval["filter_phrase_names"] = filter_phrase_names

        if self._semantic_cache is not None and cache_allowed:
            _trace({"stage": "cache", "hit": False})
        # Ограничение применяется ТОЛЬКО к тому, что уходит в промпт. Список источников
        # строится из всех строк обхода: сузить его здесь означало бы ещё и сузить
        # атрибуцию, а это отдельная величина, из которой читаются метрики осей.
        evidence_rows = (
            skeleton_rows
            if cfg_evidence_max_lines is None
            else skeleton_rows[: cfg_evidence_max_lines]
        )
        if cfg_evidence_max_lines is not None and len(evidence_rows) != len(skeleton_rows):
            _trace(
                {
                    "stage": "evidence_limited",
                    "limit": cfg_evidence_max_lines,
                    "rows_before": len(skeleton_rows),
                    "rows_after": len(evidence_rows),
                }
            )
        _trace({"stage": "graph", "enabled": enabled, "skeleton_rows": render_skeleton(evidence_rows)})
        _trace(
            {
                "stage": "vector",
                "candidates": [
                    {
                        "chunk_id": chunk.get("chunk_id"),
                        "rank": index + 1,
                        "score": vector_scores.get(str(chunk.get("chunk_id"))),
                        "score_before": vector_scores.get(str(chunk.get("chunk_id"))),
                    }
                    for index, chunk in enumerate(body_chunks)
                ],
            }
        )

        emit("status", {"stage": "order"})
        # Порядок задаёт ОДНА реализация (ADR-061, ADR-062). До этого шага порядок
        # складывался из двух правил сразу - константы оси графа в `build_sources` и
        # прибавки `graph_boost` к оценке, - и обе давали оси фору за счёт отсутствия
        # у неё оценки. Обе снесены.
        ordering, requested_ordering = resolve_ordering(
            cfg_ordering, reranker=self._reranker, rrf_k=cfg_rrf_k
        )
        ordering_name = ordering.name
        ordering_fallback = requested_ordering != ordering_name
        if ordering_fallback:
            # Откат обязан быть виден в трёх местах, иначе «вышло как обычно» не
            # отличить от «применилось то, что просили».
            _log.error(
                "реализация порядка %r не разрешилась, применён откат %r",
                requested_ordering,
                ordering_name,
            )
            _trace(
                {
                    "stage": "order",
                    "reason": "mixer_unavailable",
                    "requested": requested_ordering,
                    "applied": ordering_name,
                }
            )
            emit(
                "status",
                {
                    "stage": "order",
                    "reason": "mixer_unavailable",
                    "requested": requested_ordering,
                    "applied": ordering_name,
                },
            )
        order_scores: list[dict[str, Any]] = []
        score_before = {str(chunk.get("chunk_id")): float(chunk.get("score") or 0.0) for chunk in body_chunks}
        body_chunks = ordering.order(
            query,
            body_chunks,
            context={
                "vector_ranks": {
                    str(chunk.get("chunk_id")): index
                    for index, chunk in enumerate(body_chunks)
                    if not chunk.get("bridged")
                },
                "graph_ranks": {
                    str(chunk.get("chunk_id")): index
                    for index, chunk in enumerate(body_chunks)
                    if chunk.get("bridged")
                },
                "max_vector_chunks": self._max_vector_chunks,
            },
        )
        for chunk in body_chunks:
            order_scores.append(
                {
                    "chunk_id": chunk.get("chunk_id"),
                    "before": round(score_before.get(str(chunk.get("chunk_id")), 0.0), 4),
                    "after": round(float(chunk.get("score") or 0.0), 4),
                }
            )
        # Повторная сортировка здесь была бы вторым правилом порядка: реализация уже
        # вернула упорядоченный список, и «ещё раз отсортируем по оценке» означало бы,
        # что решение принимает не она.
        _trace({"stage": "order", "scores": order_scores})
        effective_retrieval["ordering_applied"] = ordering_name
        effective_retrieval["ordering_params"] = ordering.params()
        effective_retrieval["ordering_fallback"] = ordering_fallback

        context = ContextAssembly(limit).assemble(evidence_rows, body_chunks)
        retrieval_s = round(time.monotonic() - retrieval_started, 3)

        prompt = _build_prompt(query, context.text)
        _trace(
            {
                "stage": "llm",
                "prompt_tokens": estimate_tokens(prompt),
                "context_tokens": context.tokens,
                "dropped_chunks": context.dropped,
            }
        )

        if generate:
            emit("status", {"stage": "llm"})
            started = time.monotonic()
            parts: list[str] = []
            for delta in self._llm.generate(prompt, system=DEFAULT_SYSTEM_PROMPT, stream=True):
                parts.append(delta)
                emit("token", {"delta": delta})
            text = "".join(parts)
            generation_s = round(time.monotonic() - started, 3)
        else:
            text = ""
            generation_s = 0.0

        # Метка «что ось знает» строится здесь, а не внутри `build_sources`, потому что
        # причину `reachable_unused` знает только пайплайн: она читается из того, отработал ли
        # обход и были ли семена. `None` означает «карту не считали» (ось не открыта) и обязан
        # дойти до `build_sources` именно как `None`: пустой словарь выглядел бы как
        # «посчитали и не нашли», и метка стала бы `blind` там, где прибор не смотрел.
        graph_knowledge: Mapping[str, str] | None = None
        graph_knowledge_reasons: Mapping[str, str] | None = None
        if graph_known_sources is not None:
            graph_knowledge, graph_knowledge_reasons = _graph_knowledge_for_sources(
                body_chunks=body_chunks,
                skeleton_rows=evidence_rows,
                graph_known_sources=graph_known_sources,
                expanded_rows=expanded_rows,
            )
        sources = build_sources(
            body_chunks,
            skeleton_rows,
            graph_knowledge=graph_knowledge,
            graph_knowledge_reasons=graph_knowledge_reasons,
        )
        if graph_requested and graph_degraded:
            self.projection_metrics.observe_fallback(
                projection_status if projection_status != "ready" else "graph_expansion"
            )
        cache_store_allowed = not graph_requested or not enabled or not graph_degraded
        if generate and self._semantic_cache is not None and cache_allowed and cache_store_allowed:
            # store() сам отклоняет «плохие» ответы (пустой text, отказ LLM) — решение 2026-09-13.
            self._semantic_cache.store(
                embedding,
                CachedAnswer(text=text, sources=sources),
                active,
                revision=cache_revision,
            )
        # ADR-059: `max_depth_effective` - это зажим числа в диапазон, а не результат обхода,
        # поэтому объявлять его под именем "применено" нельзя. Разрешённая глубина и
        # ВЫПОЛНЕННАЯ - разные величины, и обе обязаны быть видны. Пустой список строк -
        # честный ноль, а не "не измерено": обход отработал и ничего не нашёл.
        effective_retrieval["max_depth_allowed"] = effective_depth
        effective_retrieval["hops_traversed"] = max(
            (int(row.get("depth") or 0) for row in expanded_rows),
            default=0,
        )
        # ADR-059, часть вторая: настоящие токены приходят от модели в `usage`. Оценка
        # `estimate_tokens(prompt)` остаётся в трейсе и в неё не превращается: это разные
        # числа, и подмена одного другим делает расход невидимым.
        llm_usage = getattr(self._llm, "last_usage", None)
        done: dict[str, Any] = {
            "text": text,
            "sources": sources,
            "generation_time_s": generation_s,
            "retrieval_time_s": retrieval_s,
            "total_time_s": round(time.monotonic() - total_started, 3),
            "revision": revision,
            "graph_degraded": graph_degraded,
            "projection_status": projection_status,
            "projection_revision": projection_revision,
            # Три величины, без которых отчёт неполон: ИДЕНТИЧНОСТЬ конфигурации, момент
            # её ПРИМЕНЕНИЯ и право на УСТАРЕВАНИЕ. Все три названы явно, а не выводятся из
            # версии кода: конфигурация меняется на лету, и из коммита её не восстановить.
            "profile_fingerprint": self._pinned_fingerprint,
            "profile_pinned_at": self._pinned_at,
            "profile_staleness": "immutable_for_session",
            # Что ПРИМЕНЕНО, а не что запрошено: иначе «применилось с дефолтом» и
            # «применилось как просили» неразличимы в артефакте.
            "effective_retrieval": effective_retrieval,
            # Настоящий расход токенов по модели. `None`, когда провайдер их не отдал, а
            # не `0`: отсутствие данных и нулевой расход - разные результаты.
            "usage": dict(llm_usage) if isinstance(llm_usage, dict) and llm_usage else None,
            # Имена параметров, ушедших в дефолт. Пустой список — тоже факт: значит
            # применено ровно то, что объявлено.
            "config_fallbacks": config_fallbacks,
            # Что заняло бюджет контекста. Без этих чисел нельзя отличить "граф вытеснил
            # текст документа" от "граф занял своё место": скелет входит первым и не
            # вытесняется, а тело получает остаток. На кэш-попадании блок равен None,
            # а не нулям: кэш отдаёт чужой ответ, и "сборка не выполнялась" - это
            # отсутствие данных, а не нулевой расход.
            "context": {
                "skeleton_count": context.skeleton_count,
                "body_count": context.body_count,
                "dropped_chunks": context.dropped,
                "skeleton_tokens": context.skeleton_tokens,
                "body_tokens": context.body_tokens,
                "total_tokens": context.tokens,
                "max_tokens": context.max_tokens,
                "skeleton_token_share": (
                    round(context.skeleton_tokens / context.tokens, 4)
                    if context.tokens
                    else None
                ),
                # Что именно модель увидела от графа. Без этого отчёт отвечает на вопрос
                # "сколько строк было", но не на "что в них было", а вопрос "помогает ли
                # граф" без второго не имеет ответа.
                "skeleton_lines": list(context.skeleton_lines),
            },
        }
        if self._semantic_cache is not None and cache_allowed:
            done["cache_hit"] = False
            done["cache_lookup_s"] = cache_lookup_s
        _trace({"stage": "done", "total_time_s": done["total_time_s"]})
        if trace:
            done["trace"] = trace_events
        emit("done", done)
        return done

    def _load_profile(self, domain: str) -> dict[str, Any]:
        """Профиль, ЗАКРЕПЛЁННЫЙ на весь срок жизни пайплайна (сессии).

        Не кэш ради скорости, а решение о моменте применения: конфигурация неизменяема в
        рамках сессии, поэтому берётся один раз и дальше только выдаётся. Три следствия,
        каждое проверено на прошлом поведении:

        * **Воспроизводимость.** Сессия измеряет ровно тот конфиг, который закреплён, даже
          если файл на хосте изменили посреди прогона. Раньше профиль перечитывался на
          каждый запрос (bind mount + чтение файла Config Service на каждый HTTP-запрос,
          кэша в загрузчике нет), то есть два вопроса одного прогона могли быть измерены
          разными настройками без следа.
        * **Латентность.** HTTP-запрос к Config Service с трёхсекундным таймаутом уходит из
          критического пути пользовательского запроса.
        * **Наблюдаемость.** Отпечаток закреплённого профиля — один на сессию, а не
          вычисляется постфактум, и `profile_fingerprint` в ответе стабилен.

        Дрейф между двумя режимами прогона ловится снаружи: они собирают разные пайплайны,
        поэтому у каждого свой отпечаток, и их сравнивает eval-раннер.
        """
        if self._pinned_profile is None:
            self._pinned_profile = self._fetch_profile(domain)
            self._pinned_fingerprint = profile_fingerprint(self._pinned_profile)
            # Момент применения фиксируется при ЗАКРЕПЛЕНИИ, а не при каждом ответе: он
            # относится к конфигурации, и «когда применён этот конфиг» — один вопрос с
            # одним ответом на всю сессию.
            self._pinned_at = datetime.now(UTC).isoformat(timespec="seconds")
        return self._pinned_profile

    def _fetch_profile(self, domain: str) -> dict[str, Any]:
        try:
            profile = self._profiles.load(domain)
        except ProfileError:
            if self._strict_profile:
                raise
            return {"retrieval": {}, "context_assembly": {}, "ontology": {}}
        verdict = validate_retrieval_profile(profile)
        if verdict.errors and PROFILE_PROBLEM_LOG.due(domain, "retrieval_error", verdict.errors):
            _log.error(
                "профиль домена %r нарушает контракт запроса: %s",
                domain,
                "; ".join(verdict.errors),
            )
        if verdict.warnings and PROFILE_PROBLEM_LOG.due(
            domain, "retrieval_warning", verdict.warnings
        ):
            _log.warning("профиль домена %r: %s", domain, "; ".join(verdict.warnings))
        if self._strict_profile and verdict.errors:
            raise ProfileError(
                f"профиль домена {domain!r} нарушает контракт запроса: " + "; ".join(verdict.errors)
            )
        if self._strict_profile:
            _validate_runtime_profile(profile)
        return profile

    @property
    def profile_fingerprint(self) -> str | None:
        """Отпечаток закреплённого конфига, `None` пока пайплайн ни разу не работал."""
        return self._pinned_fingerprint


def _context_limit(profile: dict[str, Any]) -> int:
    try:
        value = int((profile.get("context_assembly") or {}).get("max_tokens", CONTEXT_TOKEN_LIMIT))
    except (TypeError, ValueError):
        return CONTEXT_TOKEN_LIMIT
    return value if value > 0 else CONTEXT_TOKEN_LIMIT


def _build_prompt(query: str, context_text: str) -> str:
    return (
        f"Контекст:\n{context_text}\n\n"
        f"Вопрос: {query}\n\n"
        "Ответ:"
    )
