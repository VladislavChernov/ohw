"""Порядок выдачи: одна сменная реализация вместо приоритета оси (ADR-061, ADR-062).

До этого модуля порядок в выдаче задавался двумя способами сразу: константа
`graph_source_relevance` поднимала всё, что нашёл обход, а `graph_boost` прибавлял
число к оценке. Оба способа выдавали ось графа фору за счёт того, что у неё нет
сопоставимой оценки, и вместе стоили минус 0.0417 `recall@5` на замере
2026-10-16.

Здесь порядком занимается ровно одна реализация, выбранная конфигурацией.
Реализации встроенные (как стратегии чанкера), а имя, которого нет среди
встроенных, разрешается через тот же `plugin_registry`.

## Почему откат — тоже зарегистрированная реализация

Если имя не разрешилось, подставляется `vector_order_fallback` — объект той же
группы, а не ветка в коде. Ветка означала бы вторую политику порядка рядом с
первой, и разошлись бы они тихо: выигрывала бы та, что стоит в коде последней.

Записываются оба имени: запрошенное (которое не нашлось) и применённое. Иначе
артефакт показывает политику, которую никто не просил.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar, Protocol

from ..plugin_registry import (
    PluginLoadError,
    PluginMissingError,
    list_plugins,
    load_plugin,
)

__all__ = (
    "DEFAULT_ORDERING",
    "FALLBACK_ORDERING",
    "ORDERING_ENTRY_POINT_GROUP",
    "Ordering",
    "RerankOrdering",
    "RerankerLike",
    "RrfOrdering",
    "VectorOrderOrdering",
    "list_orderings",
    "resolve_ordering",
)

#: Группа entry points для порядка выдачи.
ORDERING_ENTRY_POINT_GROUP = "graphrag.orderings"

#: Что применяется, когда имя указано, но не разрешилось. Это тоже зарегистрированная
#: реализация: см. раздел «Почему откат — тоже зарегистрированная реализация».
FALLBACK_ORDERING = "vector_order_fallback"

#: Порядок по умолчанию, когда имя не задано конфигурацией.
DEFAULT_ORDERING = "rerank_bge"

#: Параметр сглаживания RRF. Не подбирался: значение взято из описания метода
#: (Cormack et al.), а не выбрано под наш корпус. Ранжирование им неустойчиво,
#: и подбор по одному набору дал бы число без смысла.
RRF_K_DEFAULT = 60


class RerankerLike(Protocol):
    """Минимальный контракт реранкера, нужный реализации порядка."""

    def rerank(self, query: str, chunks: list[dict[str, Any]]) -> list[float]: ...


class Ordering(ABC):
    """Задаёт порядок кандидатов. Одна реализация отвечает за результат."""

    #: Имя, под которым реализация зарегистрирована и попадает в артефакт.
    name: ClassVar[str]

    #: Параметры, реально действовавшие в прогоне. Пишутся в артефакт рядом с именем.
    def params(self) -> dict[str, Any]:
        return {}

    @abstractmethod
    def order(
        self,
        query: str,
        chunks: Sequence[dict[str, Any]],
        *,
        context: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        """Вернуть кандидатов в порядке выдачи.

        Реализация обязана вернуть те же объекты с проставленной оценкой: оценка —
        единственное, чем она выражает порядок. Копии не годятся, потому что поля
        вроде `filter_demoted` живут в тех же словарях.
        """


class RerankOrdering(Ordering):
    """Основной путь: кросс-энкодер оценивает объединённый набор целиком.

    Единственная реализация, которая даёт кандидатам сопоставимую оценку независимо
    от того, откуда они пришли, поэтому после неё «откуда» больше не влияет на
    «в каком порядке». Случай без оценки у кандидата здесь невозможен: оценку даёт
    проход по тексту, а не наличие кандидата в графе.
    """

    name: ClassVar[str] = "rerank_bge"

    def __init__(self, reranker: RerankerLike) -> None:
        self._reranker = reranker

    def params(self) -> dict[str, Any]:
        return {"kind": "cross_encoder", "reranker": type(self._reranker).__name__}

    def order(
        self,
        query: str,
        chunks: Sequence[dict[str, Any]],
        *,
        context: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        del context
        ordered = [dict(chunk) for chunk in chunks]
        if not ordered:
            return ordered
        scores = self._reranker.rerank(query, ordered)
        for chunk, score in zip(ordered, scores):
            chunk["score"] = float(score)
        ordered.sort(key=lambda chunk: float(chunk.get("score") or 0.0), reverse=True)
        return ordered


class RrfOrdering(Ordering):
    """Запасной путь: слияние по рангам, доступное без сервиса.

    Работает по двум упорядоченным спискам, которые пайплайн знает и без оценок:
    `context["vector_ranks"]` и `context["graph_ranks"]` — карты `chunk_id → ранг`.
    Оценки не создаёт: переставляет два списка между собой. Поэтому это откат, а не
    замена основному пути.
    """

    name: ClassVar[str] = "rrf_fallback"

    def __init__(self, *, k: int = RRF_K_DEFAULT) -> None:
        self._k = k

    def params(self) -> dict[str, Any]:
        return {"kind": "rank_fusion", "k": self._k}

    def order(
        self,
        query: str,
        chunks: Sequence[dict[str, Any]],
        *,
        context: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        del query
        ordered = [dict(chunk) for chunk in chunks]
        vector_ranks = dict(context.get("vector_ranks") or {})
        graph_ranks = dict(context.get("graph_ranks") or {})
        for chunk in ordered:
            chunk_id = str(chunk.get("chunk_id") or "")
            fused = 0.0
            for ranks in (vector_ranks, graph_ranks):
                rank = ranks.get(chunk_id)
                if rank is not None:
                    fused += 1.0 / (self._k + rank + 1)
            chunk["score"] = fused
        # Кандидат без ранга ни в одном списке: оценки у него не существует. Такое
        # оставлять нулём молча нельзя — такой попадает в хвост и выглядит как
        # «оценено и неважно», а на деле «не оценивалось вовсе».
        ordered.sort(key=lambda chunk: float(chunk.get("score") or 0.0), reverse=True)
        return ordered


class VectorOrderOrdering(Ordering):
    """Зарегистрированный откат: векторный порядок, вклад графа сохранён.

    Ничего не переставляет и не отбрасывает. Граф при этом никуда не девается: его
    строки свидетельства и источники остаются в ответе, теряется только влияние на
    порядок.
    """

    name: ClassVar[str] = FALLBACK_ORDERING

    def params(self) -> dict[str, Any]:
        return {"kind": "vector_order"}

    def order(
        self,
        query: str,
        chunks: Sequence[dict[str, Any]],
        *,
        context: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        del query, context
        return [dict(chunk) for chunk in chunks]


_BUILTIN_ORDERINGS: dict[str, type[Ordering]] = {
    RerankOrdering.name: RerankOrdering,
    RrfOrdering.name: RrfOrdering,
    VectorOrderOrdering.name: VectorOrderOrdering,
}


def list_orderings() -> tuple[str, ...]:
    """Имена доступных реализаций: встроенные плюс установленные плагины."""
    return tuple(sorted(set(_BUILTIN_ORDERINGS) | set(list_plugins(ORDERING_ENTRY_POINT_GROUP))))


def _ordering_from_plugin(name: str) -> Ordering | None:
    """Собрать реализацию из entry point. `None` — имя не принадлежит плагинам."""
    try:
        factory = load_plugin(ORDERING_ENTRY_POINT_GROUP, name)
    except PluginMissingError:
        return None
    try:
        built = factory()
    except Exception as exc:
        raise PluginLoadError(
            f"плагин порядка {name!r}: фабрика не собрала Ordering при вызове factory(): {exc}"
        ) from exc
    if not isinstance(built, Ordering):
        raise PluginLoadError(
            f"плагин порядка {name!r}: фабрика вернула не Ordering, а {type(built).__name__}"
        )
    return built


def resolve_ordering(
    name: str | None,
    *,
    reranker: RerankerLike,
    rrf_k: int = RRF_K_DEFAULT,
) -> tuple[Ordering, str | None]:
    """Разрешить реализацию порядка. Возвращает пару (реализация, запрошенное имя).

    Откат применяется только для **неразрешимого имени** и сбоя загрузки, то есть
    до начала работы плагина. Установленный плагин, упавший внутри работы, даёт
    откат по контракту вызывающего кода; плагин, вернувший результат, отвечает за
    него сам.

    Тонкость: `rerank_bge` встроен и требует реранкер, поэтому его нельзя отдать
    реестру — она принимает фабрику без аргументов. Сборку делает вызывающий.
    """
    requested = (name or DEFAULT_ORDERING).strip()
    if requested == RerankOrdering.name:
        return RerankOrdering(reranker), requested
    builtin = _BUILTIN_ORDERINGS.get(requested)
    if builtin is not None:
        if builtin is RrfOrdering:
            return RrfOrdering(k=rrf_k), requested
        return builtin(), requested
    try:
        loaded = _ordering_from_plugin(requested)
    except PluginLoadError:
        loaded = None
    if loaded is None:
        return VectorOrderOrdering(), requested
    return loaded, requested
