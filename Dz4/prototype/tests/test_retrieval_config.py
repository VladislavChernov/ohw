"""Конфигурация запроса: закрепление, именованные fallback'ы, отпечаток.

Проверяются свойства, а не строки. Ключевое здесь — достижимость каждого fallback'а:
раньше обработчик `graph_boost` был написан, но недостижим, потому что то же поле
вычислялось раньше в теле trace-события. Написанный обработчик и работающий обработчик —
разные утверждения, и тест на достижимость — единственный способ их различить.

Граф в тестах непустой и расширение действительно происходит: на пустом графе код честно
ставит `graph_degraded` с причиной `no_context_ids`, и тест на «дефект конфига не деградирует
ось графа» прошёл бы впустую.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from graphrag_proto.retrieval.adapters.deterministic import DeterministicEmbedder
from graphrag_proto.retrieval.adapters.inmemory import (
    InMemoryGraphStore,
    InMemoryVectorStore,
)
from graphrag_proto.retrieval.adapters.llm import FakeLLM
from graphrag_proto.retrieval.adapters.reranker import NoOpRerankerAdapter
from graphrag_proto.retrieval.pipeline import QueryPipeline, profile_fingerprint
from graphrag_proto.retrieval.profile import ProfileError

VALID_RETRIEVAL: dict[str, Any] = {
    "graph_search_enabled": True,
    "expansion_direction": "parent",
    "max_depth": 2,
    "max_fanout": 4,
    "max_graph_nodes": 8,
    "graph_boost": 0.2,
}


class _ExpansionGraph(InMemoryGraphStore):
    """Граф, который на самом деле расширяется и записывает применённые параметры."""

    def __init__(self) -> None:
        super().__init__()
        self.expansion_calls: list[dict[str, Any]] = []

    def expand(
        self,
        context_ids: list[str],
        *,
        direction: str = "both",
        kinds: Sequence[str] | None = None,
        max_depth: int = 2,
        max_fanout: int = 8,
        max_nodes: int = 32,
    ) -> list[dict[str, Any]]:
        self.expansion_calls.append(
            {
                "seeds": list(context_ids),
                "direction": direction,
                "kinds": list(kinds) if kinds is not None else None,
                "max_depth": max_depth,
                "max_fanout": max_fanout,
                "max_nodes": max_nodes,
            }
        )
        return [
            {
                "node_id": "tag:it:district",
                "canonical_name": "District",
                "path": list(context_ids) + ["tag:it:district"],
                "depth": 1,
                "origin": "graph",
                "confidence": 0.9,
                "kind": "MENTIONS",
                "source_ids": ["src://doc"],
            }
        ]


class _Loader:
    """Загрузчик профиля с изменяемым содержимым — нужно, чтобы проверить закрепление."""

    def __init__(self, retrieval: dict[str, Any] | None = None) -> None:
        self.retrieval = retrieval if retrieval is not None else dict(VALID_RETRIEVAL)
        self.loads = 0

    def active_domain(self) -> str:
        return "it"

    def load(self, domain: str | None = None) -> dict[str, Any]:
        self.loads += 1
        return {"profile": {"name": domain or "it"}, "retrieval": dict(self.retrieval)}


class _RawLoader:
    """Отдаёт профиль как есть — для проверки не-mapping секций."""

    def __init__(self, profile: Any) -> None:
        self._profile = profile

    def active_domain(self) -> str:
        return "it"

    def load(self, domain: str | None = None) -> dict[str, Any]:
        return self._profile  # type: ignore[no-any-return]


class _ExplodingLoader(_Loader):
    def load(self, domain: str | None = None) -> dict[str, Any]:
        self.loads += 1
        raise ProfileError("Config Service недоступен")


def _pipeline(loader: Any, graph: Any = None, **kwargs: Any) -> QueryPipeline:
    vector = InMemoryVectorStore()
    embedding = DeterministicEmbedder().embed("Quicksort", "it")
    vector.upsert_vectors(
        [
            {
                "chunk_id": "chk:1",
                "embedding": embedding,
                "metadata": {
                    "text": "Quicksort",
                    "source_url": "src://doc",
                    "domain": "it",
                    "context_ids": ["tag:it:quicksort"],
                },
            }
        ]
    )
    return QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=graph if graph is not None else _ExpansionGraph(),
        vector_store=vector,
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="answer"),
        profile_loader=loader,
        **kwargs,
    )


def _run(pipe: QueryPipeline, **kwargs: Any) -> dict[str, Any]:
    return pipe.run("Quicksort", domain="it", generate=False, **kwargs)


# --- закрепление на старте сессии ---------------------------------------------


def test_profile_is_pinned_for_the_session_not_read_per_request() -> None:
    """Конфигурация неизменяема в рамках сессии, поэтому берётся один раз.

    Раньше профиль перечитывался на каждый запрос (bind mount + чтение файла Config
    Service на каждый HTTP-запрос, кэша в загрузчике нет). Два вопроса одного прогона могли
    быть измерены разными настройками без следа, а HTTP-запрос с трёхсекундным таймаутом
    стоял на критическом пути пользовательского запроса.
    """
    loader = _Loader()
    pipe = _pipeline(loader)

    first = _run(pipe)
    second = _run(pipe)

    assert loader.loads == 1
    assert first["profile_fingerprint"] == second["profile_fingerprint"]
    assert first["profile_pinned_at"] == second["profile_pinned_at"]


def test_pinned_profile_survives_change_of_the_source() -> None:
    """Правка файла на хосте посреди сессии не меняет применённые настройки.

    Это и есть смысл закрепления: не «ускорение», а решение о моменте применения. Тест
    моделирует ровно то событие, ради которого закрепление введено, и без закрепления
    второй вопрос был бы измерен с другим `graph_boost`.
    """
    loader = _Loader()
    pipe = _pipeline(loader)

    first = _run(pipe)
    loader.retrieval = {**VALID_RETRIEVAL, "graph_boost": 0.9, "max_depth": 5}
    second = _run(pipe)

    assert first["profile_fingerprint"] == second["profile_fingerprint"]
    assert second["effective_retrieval"]["graph_boost"] == 0.2
    assert second["effective_retrieval"]["max_depth"] == 2
    assert loader.loads == 1


def test_response_names_identity_moment_and_staleness_right() -> None:
    """Три величины названы явно: идентичность, момент применения, право на устаревание.

    Без них отчёт неполон по построению: версия кода конфигурацию не определяет, а изменить
    её посреди прогона можно было всегда.
    """
    pipe = _pipeline(_Loader())

    done = _run(pipe)

    assert done["profile_fingerprint"]
    assert done["profile_pinned_at"]
    assert done["profile_staleness"] == "immutable_for_session"
    assert pipe.profile_fingerprint == done["profile_fingerprint"]


def test_fingerprint_is_stable_and_sensitive() -> None:
    """Один и тот же профиль — один отпечаток, разный — разный.

    Иначе отпечаток бесполезен: он должен различать «тот же конфиг» и «другой». Ключи в
    разном порядке — тот же конфиг: иначе отпечаток менялся бы от правки форматирования
    YAML, а не от смысла.
    """
    base = {"retrieval": {"max_depth": 2}}

    assert profile_fingerprint(base) == profile_fingerprint({"retrieval": {"max_depth": 2}})
    assert profile_fingerprint(base) != profile_fingerprint({"retrieval": {"max_depth": 3}})
    assert profile_fingerprint({"a": 1, "b": 2}) == profile_fingerprint({"b": 2, "a": 1})


# --- именованные fallback'ы ----------------------------------------------------


@pytest.mark.parametrize(
    ("key", "value", "expected_default"),
    [
        ("graph_boost", "много", 0.0),
        ("max_depth", "глубоко", 2),
        ("max_fanout", [1], 8),
        ("max_graph_nodes", None, 5),
        ("graph_source_relevance", "высоко", 1.0),
    ],
)
def test_unreadable_value_becomes_named_fallback_and_graph_still_works(
    key: str, value: Any, expected_default: Any
) -> None:
    """Опечатка в конфиге стоит дефолта по этому параметру, а не отказа всей оси графа.

    Раньше `int()`/`float()` падали, попадали в широкий `except` расширения и вопрос выпадал
    из измеренного среза целиком: одна негодная строка профиля обнуляла вклад графа. Здесь
    дефект конфига и отказ рантайма разделены — дефолт с именем против деградации.
    """
    graph = _ExpansionGraph()
    pipe = _pipeline(_Loader({**VALID_RETRIEVAL, key: value}), graph=graph)

    done = _run(pipe)

    assert f"retrieval.{key}" in done["config_fallbacks"]
    assert done["graph_degraded"] is False
    assert done["effective_retrieval"][key] == expected_default
    # расширение состоялось, то есть дефолт по одному параметру не отключил ось
    assert len(graph.expansion_calls) == 1


def test_fallback_default_is_what_the_expander_actually_got() -> None:
    """Дефолт должен дойти до адаптера, а не остаться в отчёте.

    Иначе артефакт показывает одно, а система применяет другое: расхождение между
    «применённым» и «применяемым» — это ровно то, ради чего чтение стало одноразовым.
    """
    graph = _ExpansionGraph()
    pipe = _pipeline(_Loader({**VALID_RETRIEVAL, "max_fanout": "много"}), graph=graph)

    _run(pipe)

    assert graph.expansion_calls[0]["max_fanout"] == 8


def test_negative_depth_is_a_named_fallback_not_a_crash() -> None:
    """`max_depth: -1` — негодное значение, а не «максимальная глубина».

    Без проверки диапазона `int()` проходит молча, и `-1` уходит в адаптер расширения как
    осмысленный параметр.
    """
    graph = _ExpansionGraph()
    pipe = _pipeline(_Loader({**VALID_RETRIEVAL, "max_depth": -1}), graph=graph)

    done = _run(pipe)

    assert "retrieval.max_depth" in done["config_fallbacks"]
    assert done["effective_retrieval"]["max_depth"] == 2
    assert graph.expansion_calls[0]["max_depth"] == 2


def test_clean_profile_has_empty_fallback_list() -> None:
    """Пустой список — тоже факт, и он проверяется: значит применено ровно объявленное.

    Без этой проверки «fallback'ов нет» и «fallback'ы не считаются» выглядят одинаково.
    """
    pipe = _pipeline(_Loader())

    done = _run(pipe)

    assert done["config_fallbacks"] == []
    assert done["effective_retrieval"]["graph_boost"] == 0.2
    assert done["effective_retrieval"]["max_depth"] == 2


def test_boost_fallback_is_reachable_with_trace_off() -> None:
    """Регрессия на тот самый мёртвый код.

    `graph_boost` вычислялся в теле trace-события, а аргумент функции вычисляется до вызова:
    при `trace=True` первое чтение падало раньше терпимой ветки, и обработчик не срабатывал
    никогда. Значит «работает» зависело от отладочного флага, и тест обязан ловить именно
    это — обе ветки проверяются, потому что ломалась только одна.
    """
    pipe = _pipeline(_Loader({**VALID_RETRIEVAL, "graph_boost": "много"}))

    for trace in (False, True):
        done = pipe.run("Quicksort", domain="it", generate=False, trace=trace)
        assert "retrieval.graph_boost" in done["config_fallbacks"], f"trace={trace}"
        assert done["graph_degraded"] is False, f"trace={trace}"
        assert done["effective_retrieval"]["graph_boost"] == 0.0, f"trace={trace}"


def test_applied_boost_available_without_trace() -> None:
    """Применённый boost должен быть в ответе всегда, а не только при включённом trace.

    Eval-раннер читал его из trace-события, поэтому при выключенном trace поле было всегда
    `None` — запись о применённой настройке зависела от отладочного флага.
    """
    pipe = _pipeline(_Loader())

    done = _run(pipe, trace=False)

    assert done["effective_retrieval"]["graph_boost"] == 0.2


# --- контракт и строгий режим --------------------------------------------------


def test_strict_mode_raises_on_unreadable_retrieval_value() -> None:
    """В строгом режиме (eval) нечитаемое значение — ошибка, а не тихий дефолт.

    В обычном режиме это дефолт с именем: запрос пользователя получает ответ, и отчёт
    показывает, что применено. В eval-режиме конфиг должен быть безупречным, иначе измеряется
    не то, что объявлено, — и молчаливый дефолт сделал бы прогон бессмысленным.
    """
    pipe = _pipeline(_Loader({**VALID_RETRIEVAL, "max_depth": "глубоко"}), strict_profile=True)

    with pytest.raises(ProfileError, match="retrieval.max_depth"):
        _run(pipe)


def test_non_mapping_retrieval_section_raises_in_strict_mode() -> None:
    """`retrieval: "строка"` в обычном режиме ронял запрос AttributeError.

    Теперь это названная ошибка профиля, а не падение с AttributeError на `.get`.
    """
    pipe = _pipeline(_RawLoader({"retrieval": "строка"}), strict_profile=True)

    with pytest.raises(ProfileError, match="retrieval должен быть mapping"):
        _run(pipe)


def test_load_failure_in_non_strict_mode_falls_back_to_defaults_visibly() -> None:
    """Падение загрузки в обычном режиме — дефолты, и это должно быть видно.

    Раньше это был полностью немой откат: система отвечает, все параметры кодовые. Теперь в
    ответе видно, что конфигурация не была применена, — по пустому `graph_search_enabled`.
    Граф при этом не «деградирует», а не запрашивается, и это разные вещи: `not_requested`
    против `degraded`, и путать их нельзя, иначе не найти деградацию там, где она есть.
    """
    pipe = _pipeline(_ExplodingLoader())

    done = _run(pipe)

    assert done["effective_retrieval"]["graph_search_enabled"] is False
    assert done["projection_status"] == "not_requested"
    assert done["graph_degraded"] is False
    # отпечаток есть даже при откате: применённый конфиг — это тоже факт
    assert done["profile_fingerprint"] is not None


def test_strict_mode_propagates_load_failure() -> None:
    """В строгом режиме падение загрузки останавливает прогон, а не откатывается.

    Иначе в eval первая же недоступность Config Service тихо превращала бы измерение в
    замер кодовых дефолтов, и артефакт об этом не сказал бы ничего.
    """
    pipe = _pipeline(_ExplodingLoader(), strict_profile=True)

    with pytest.raises(ProfileError):
        _run(pipe)
