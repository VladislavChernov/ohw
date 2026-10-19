"""РўСѓРјР±Р»РµСЂ РіСЂР°С„РѕРІРѕР№ РѕСЃРё (graph_search_enabled) Рё С‚Р°Р№РјРёРЅРіРё СЂРµС‚СЂРёРІР°
(add-retrieval-graph-toggle, BR-1..BR-5).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from graphrag_proto.retrieval.adapters.base import (
    MAX_EXPANSION_DEPTH,
    GraphStoreProvider,
    Reranker,
)
from graphrag_proto.retrieval.adapters.deterministic import DeterministicEmbedder
from graphrag_proto.retrieval.adapters.inmemory import InMemoryVectorStore
from graphrag_proto.retrieval.adapters.llm import FakeLLM
from graphrag_proto.retrieval.adapters.reranker import NoOpRerankerAdapter
from graphrag_proto.retrieval.pipeline import (
    FILTER_DEMOTE_SCALE,
    QueryPipeline,
    graph_search_enabled,
)
from graphrag_proto.retrieval.profile import DomainProfileLoader, ProfileError
from graphrag_proto.retrieval.semantic_cache import InMemorySemanticCache

PROFILE: dict[str, Any] = {
    "profile": {"name": "it"},
    "retrieval": {
        "graph_search_enabled": True,
        "expansion_direction": "parent",
        "max_depth": 2,
        "max_fanout": 4,
        "max_graph_nodes": 8,
        "filter_demote_scale": 0.2,
    },
    "context_assembly": {"max_tokens": 4096},
}


class _StubLoader(DomainProfileLoader):
    def __init__(self, profile: dict[str, Any]) -> None:
        super().__init__()
        self._profile = profile

    def active_domain(self) -> str:
        return "it"

    def load(self, domain: str | None = None) -> dict[str, Any]:
        return self._profile


class _CountingGraphStore(GraphStoreProvider):
    def __init__(
        self,
        expansion_rows: list[dict[str, Any]] | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.expansion_calls: list[tuple[list[str], dict[str, Any]]] = []
        self.events = events if events is not None else []
        self._expansion_rows = expansion_rows if expansion_rows is not None else [
            {
                "node_id": "tag:it:parent",
                "canonical_name": "Parent",
                "path": ["tag:it:first", "tag:it:parent"],
                "depth": 1,
                "kind": "parent",
                "origin": "user",
                "confidence": 0.9,
                "source_ids": ["src://graph"],
                "chunk_ids": ["chk:graph"],
                "domain": "it",
            }
        ]

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
        self.events.append("expand")
        self.expansion_calls.append(
            (
                list(context_ids),
                {
                    "direction": direction,
                    "kinds": list(kinds) if kinds is not None else None,
                    "max_depth": max_depth,
                    "max_fanout": max_fanout,
                    "max_nodes": max_nodes,
                },
            )
        )
        return [dict(row) for row in self._expansion_rows]

    def query(
        self,
        cypher: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        raise AssertionError("graph query must not run")

    def upsert_nodes(self, nodes: list[dict[str, Any]]) -> None:
        return None

    def upsert_edges(self, edges: list[dict[str, Any]]) -> None:
        return None

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        return None

    def delete_node(self, node_id: str) -> bool:
        return False

    def list_chunk_ids_of_source(self, source_id: str) -> list[str]:
        return []


class _FixedReranker(Reranker):
    def __init__(self, scores: list[float]) -> None:
        self._scores = scores

    def rerank(self, query: str, chunks: list[dict[str, Any]]) -> list[float]:
        return list(self._scores)


class _RecordingVectorStore(InMemoryVectorStore):
    def __init__(
        self,
        rows: list[dict[str, Any]],
        events: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.rows = rows
        self.events = events if events is not None else []
        self.calls: list[tuple[list[float], int, str | None]] = []

    def vector_search(
        self,
        embedding: list[float],
        top_k: int = 5,
        domain: str | None = None,
    ) -> list[dict[str, Any]]:
        self.events.append("vector")
        self.calls.append((embedding, top_k, domain))
        return [dict(row) for row in self.rows[:top_k]]


def _vector_rows() -> list[dict[str, Any]]:
    return [
        {
            "chunk_id": "chk:first",
            "score": 0.9,
            "text": "first",
            "source_url": "src://first",
            "domain": "it",
            "context_ids": ["tag:it:first"],
            "custom": {"language": "en"},
        },
        {
            "chunk_id": "chk:second",
            "score": 0.8,
            "text": "second",
            "source_url": "src://second",
            "domain": "it",
            "tag_ids": ["tag:it:second"],
        },
    ]


def _pipeline(
    profile: dict[str, Any],
    graph: GraphStoreProvider,
    vector_store: InMemoryVectorStore | None = None,
) -> QueryPipeline:
    return QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=graph,
        vector_store=vector_store or InMemoryVectorStore(),
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="РѕС‚РІРµС‚"),
        profile_loader=_StubLoader(profile),
    )


def test_graph_search_experiment_disabled_by_default() -> None:
    assert not graph_search_enabled({"retrieval": {}})
    assert not graph_search_enabled({})
    assert graph_search_enabled({"retrieval": {"graph_search_enabled": True}})
    assert not graph_search_enabled({"retrieval": {"graph_search_enabled": False}})


def test_graph_search_enabled_env_overrides_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    profile_on: dict[str, Any] = {"retrieval": {"graph_search_enabled": True}}
    profile_off: dict[str, Any] = {"retrieval": {"graph_search_enabled": False}}

    monkeypatch.setenv("RETRIEVAL_GRAPH_ENABLED", "false")
    assert not graph_search_enabled(profile_on)

    monkeypatch.setenv("RETRIEVAL_GRAPH_ENABLED", "TRUE")
    assert graph_search_enabled(profile_off)

    monkeypatch.delenv("RETRIEVAL_GRAPH_ENABLED")
    assert graph_search_enabled(profile_on)


class _BrokenProfileLoader(DomainProfileLoader):
    def load(self, domain: str | None = None) -> dict[str, Any]:
        raise ProfileError("profile unavailable")


def test_strict_pipeline_fails_on_profile_error() -> None:
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=_CountingGraphStore(),
        vector_store=InMemoryVectorStore(),
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="ответ"),
        profile_loader=_BrokenProfileLoader(),
        strict_profile=True,
    )
    with pytest.raises(ProfileError):
        pipe.run("база данных", domain="it", generate=False)


def test_strict_pipeline_accepts_profile_without_ontology() -> None:
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=_CountingGraphStore(),
        vector_store=_RecordingVectorStore(_vector_rows()),
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="ответ"),
        profile_loader=_StubLoader(
            {
                "profile": {"name": "it"},
                "retrieval": {"graph_search_enabled": False},
            }
        ),
        strict_profile=True,
    )

    done = pipe.run("seed", domain="it", generate=False)

    assert done["text"] == ""
    assert done["graph_degraded"] is False


def test_strict_pipeline_accepts_bounded_expansion_config() -> None:
    graph = _CountingGraphStore()
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=graph,
        vector_store=_RecordingVectorStore(_vector_rows()),
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="ответ"),
        profile_loader=_StubLoader(
            {
                "profile": {"name": "it"},
                "retrieval": {
                    "graph_search_enabled": True,
                    "expansion_direction": "related",
                    "max_depth": 1,
                    "max_fanout": 2,
                    "max_graph_nodes": 3,
                },
            }
        ),
        strict_profile=True,
    )

    done = pipe.run("seed", domain="it", generate=False)

    assert graph.expansion_calls == [
        (
            ["tag:it:first", "tag:it:second"],
            {
                "direction": "related",
                "kinds": None,
                "max_depth": 1,
                "max_fanout": 2,
                # ADR-071: до адаптера доходит бюджет НАДМНОЖЕСТВА, а не сам срез.
                # Отбор по сущностям вопроса обязан идти до среза, и для этого нужно, чтобы
                # было из чего выбирать: при max_graph_nodes=3 это 3 * 4 = 12.
                "max_nodes": 12,
            },
        )
    ]
    assert done["graph_degraded"] is False


def _row(name: str, chunk_id: str, source: str) -> dict[str, Any]:
    return {
        "node_id": f"tag:it:{name}",
        "canonical_name": name,
        "path": ["tag:it:seed", f"tag:it:{name}"],
        "depth": 1,
        "kind": "REFERENCES",
        "origin": "user",
        "confidence": 0.9,
        "source_ids": [source],
        "chunk_ids": [chunk_id],
        "domain": "it",
    }


def test_corroborated_nodes_survive_the_cut_and_are_reported() -> None:
    """Отбор по сущностям вопроса идёт ДО среза (ADR-071).

    Набор подставлен так, что подтверждённая сущность стоит последней: при отборе после
    среза она была бы уже отброшена, и ось графа не принесла бы ничего. Здесь она обязана
    выжить, а её имя должно совпасть с текстом вопроса.
    """
    rows = [
        _row("alpha", "chk:a", "src://one"),
        _row("beta", "chk:b", "src://two"),
        _row("gamma", "chk:c", "src://three"),
        _row("omega", "chk:w", "src://four"),
    ]
    graph = _CountingGraphStore(expansion_rows=rows)
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=graph,
        vector_store=_RecordingVectorStore(_vector_rows()),
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="ответ"),
        profile_loader=_StubLoader(
            {
                "profile": {"name": "it"},
                "retrieval": {
                    "graph_search_enabled": True,
                    "expansion_direction": "both",
                    "max_depth": 1,
                    "max_fanout": 4,
                    "max_graph_nodes": 1,
                },
            }
        ),
        strict_profile=True,
    )

    done = pipe.run("про omega и прочее", domain="it", generate=False)

    effective = done["effective_retrieval"]
    assert effective["expansion_candidates"] == 4, "обход обязан отдать всё надмножество"
    assert effective["expansion_corroborated"] == 1, "подтвердиться должно одно имя"
    assert effective["expansion_candidate_budget"] == 4, "бюджет = срез × 4"
    # Срез в 1 строку, и выживает именно подтверждённая сущность, а не первая по счёту.
    effective_retrieval_sources = {
        str(item.get("source_url")) for item in done.get("sources", []) if item
    }
    assert "src://four" in effective_retrieval_sources, (
        "подтверждённая сущность обязана дойти до источников"
    )


def test_expansion_kinds_reach_the_adapter_and_narrowing_reason_is_reported() -> None:
    """Сужение по видам доходит до адаптера, и пустой результат честно называет причину."""
    graph = _CountingGraphStore(expansion_rows=[])
    profile = {
        **PROFILE,
        "retrieval": {**PROFILE["retrieval"], "expansion_kinds": ["REQUIRES_CONSTRAINT"]},
    }
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=graph,
        vector_store=_RecordingVectorStore(_vector_rows()),
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="ответ"),
        profile_loader=_StubLoader(profile),
    )

    result = pipe.run("seed", trace=True)

    assert graph.expansion_calls[0][1]["kinds"] == ["REQUIRES_CONSTRAINT"]
    assert result["graph_degraded"] is True
    assert result["trace"][1]["reason"] == "no_matching_edge_kinds"


def test_expansion_kinds_any_means_no_narrowing_and_reports_empty_projection() -> None:
    """`any` снимает сужение, поэтому пустой результат — это отсутствие рёбер, а не промах сужения."""
    graph = _CountingGraphStore(expansion_rows=[])
    profile = {
        **PROFILE,
        "retrieval": {**PROFILE["retrieval"], "expansion_kinds": ["any"]},
    }
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=graph,
        vector_store=_RecordingVectorStore(_vector_rows()),
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="ответ"),
        profile_loader=_StubLoader(profile),
    )

    result = pipe.run("seed", trace=True)

    assert graph.expansion_calls[0][1]["kinds"] == ["any"]
    assert result["graph_degraded"] is True
    assert result["trace"][1]["reason"] == "empty_projection"


def test_empty_graph_projection_is_degraded_and_not_cached() -> None:
    cache = InMemorySemanticCache(threshold=0.80, ttl_s=0)
    graph = _CountingGraphStore(expansion_rows=[])
    vector = _RecordingVectorStore(_vector_rows())
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=graph,
        vector_store=vector,
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="ответ"),
        profile_loader=_StubLoader(PROFILE),
        semantic_cache=cache,
    )

    first = pipe.run("seed", trace=True)
    assert first["graph_degraded"] is True
    assert first["trace"][1]["reason"] == "empty_projection"
    assert cache.stats()["entries"] == 0

    pipe.run("seed")
    assert cache.stats()["entries"] == 0


def test_no_constant_reaches_candidate_without_own_score() -> None:
    """Константа не достаёт до кандидата, у которого нет собственной оценки.

    Раньше параметр `graph_boost` прибавлял число к оценке ПОСЛЕ ранжирования, и буст
    доставался всем кандидатам, чей источник был достигнут обходом. Векторный кандидат
    при этом терял часть своей оценки, а кандидат из моста, у которого оценки нет вовсе,
    получал от константы ВСЮ свою оценку — фору ровно за то, что он ничего не оценивал.

    Проверка сформулирована как инвариант, а не как «параметра быть не должно»: запрет
    конкретного имени ломается на первом же законном изменении и учит обходить тест.
    """
    vector = _RecordingVectorStore(_vector_rows())
    vector.upsert_vectors(
        [
            {
                "chunk_id": "chk:graph",
                "embedding": [0.1],
                "text": "текст достигнутого обходом чанка",
                "metadata": {"source_url": "src://graph", "domain": "it"},
            }
        ]
    )
    recorder = _ChunkRecordingReranker()
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=_CountingGraphStore(expansion_rows=_expansion_rows_reaching("chk:graph")),
        vector_store=vector,
        reranker=recorder,
        llm=FakeLLM(text="ответ"),
        profile_loader=_StubLoader(PROFILE),
    )

    pipe.run("seed", generate=False, trace=True)

    bridged = [chunk for chunk in recorder.seen if chunk.get("bridged")]
    assert bridged, "нужен кандидат без собственной оценки - тест обязан его получить"
    for chunk in bridged:
        assert float(chunk.get("score") or 0.0) == pytest.approx(0.0), (
            "кандидат без собственной оценки получил её из константы"
        )
    # Векторные кандидаты свою оценку сохраняют: инвариант про константу, а не про
    # «у всех ноль».
    vector_scored = [
        float(chunk.get("score") or 0.0) for chunk in recorder.seen if not chunk.get("bridged")
    ]
    assert vector_scored, "векторные кандидаты должны получить оценку"
    assert all(score > 0.0 for score in vector_scored)


def test_ordering_names_are_recorded_in_the_artifact() -> None:
    """Применённая реализация порядка пишется в артефакт вместе с параметрами.

    Конфигурация меняется на лету, и из коммита имя не восстановить. Без этих полей
    отчёт показывает политику, которую никто не просил.
    """
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=_CountingGraphStore(),
        vector_store=_RecordingVectorStore(_vector_rows()),
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="ответ"),
        profile_loader=_StubLoader(PROFILE),
    )

    effective = pipe.run("seed", generate=False)["effective_retrieval"]

    assert effective["ordering_requested"] == "rerank_bge"
    assert effective["ordering_applied"] == "rerank_bge"
    assert effective["ordering_fallback"] is False
    assert effective["ordering_params"]


def test_unresolvable_ordering_falls_back_to_registered_implementation() -> None:
    """Неразрешимое имя даёт зарегистрированный откат, а не возврат к приоритету оси.

    Записаны ОБА имени: без запрошенного отчёт не покажет, что просили не то.
    """
    profile = {
        **PROFILE,
        "retrieval": {**PROFILE["retrieval"], "ordering": "нет_такой_реализации"},
    }
    events: list[tuple[str, dict[str, Any]]] = []
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=_CountingGraphStore(),
        vector_store=_RecordingVectorStore(_vector_rows()),
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="ответ"),
        profile_loader=_StubLoader(profile),
    )

    done = pipe.run(
        "seed",
        generate=False,
        emit=lambda kind, payload: events.append((kind, payload)),
    )

    effective = done["effective_retrieval"]
    assert effective["ordering_requested"] == "нет_такой_реализации"
    assert effective["ordering_applied"] == "vector_order_fallback"
    assert effective["ordering_fallback"] is True
    # Откат виден и в событии, а не только в артефакте.
    statuses = [
        payload
        for kind, payload in events
        if kind == "status" and payload.get("stage") == "order"
    ]
    assert statuses, "откат порядка обязан быть виден в событиях"


def test_rrf_ordering_fuses_two_rank_lists_without_scores() -> None:
    """Запасная реализация сливает два списка по рангам, не выдумывая оценок.

    Проверяется и что она даёт оценку всем кандидатам, включая пришедшего из моста, и
    что кросс-энкодер при этом НЕ зовётся: RRF не должен превращаться во «вторую
    политику, которая тихо перезаписала бы результат».
    """
    profile = {
        **PROFILE,
        "retrieval": {**PROFILE["retrieval"], "ordering": "rrf_fallback"},
    }
    recorder = _ChunkRecordingReranker()
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=_CountingGraphStore(expansion_rows=_expansion_rows_reaching("chk:graph")),
        vector_store=_vector_store_with_graph_chunk(),
        reranker=recorder,
        llm=FakeLLM(text="ответ"),
        profile_loader=_StubLoader(profile),
    )

    done = pipe.run("seed", generate=False, trace=True)
    effective = done["effective_retrieval"]

    assert effective["ordering_applied"] == "rrf_fallback"
    assert effective["ordering_fallback"] is False
    assert effective["ordering_params"]["kind"] == "rank_fusion"
    # Кросс-энкодер не вызывался: у RRF нет оценок, и он их не изобретает.
    assert not recorder.seen, "RRF не должен звать реранкер"
    order_event = next(
        event for event in done["trace"] if event.get("stage") == "order" and "scores" in event
    )
    scores = {item["chunk_id"]: item["after"] for item in order_event["scores"]}
    k = int(effective["ordering_params"]["k"])
    # Векторные кандидаты по своим рангам, мостовый - по графовому. Все оценки положительны:
    # «нет оценки» и «оценка нулевая» - разные вещи. Допуск по округлению: в трейсе
    # оценки пишутся с четырьмя знаками.
    assert scores["chk:first"] == pytest.approx(1.0 / (k + 0 + 1), abs=1e-4)
    assert scores["chk:second"] == pytest.approx(1.0 / (k + 1 + 1), abs=1e-4)
    assert scores["chk:graph"] > 0.0, "мостовый кандидат обязан получить оценку RRF"


def _vector_store_with_graph_chunk() -> _RecordingVectorStore:
    """Хранилище, где вектор вернул два чанка, а третий достаётся только через мост."""
    vector = _RecordingVectorStore(_vector_rows())
    vector.upsert_vectors(
        [
            {
                "chunk_id": "chk:graph",
                "embedding": [0.1],
                "text": "текст достигнутого обходом чанка",
                "metadata": {"source_url": "src://graph", "domain": "it"},
            }
        ]
    )
    return vector


def _vector_score(chunk_id: str) -> float:
    for row in _vector_rows():
        if str(row["chunk_id"]) == str(chunk_id):
            return float(row["score"])
    return 0.0

def test_pipeline_disabled_preserves_vector_baseline() -> None:
    profile: dict[str, Any] = {
        **PROFILE,
        "retrieval": {**PROFILE["retrieval"], "graph_search_enabled": False},
    }
    graph = _CountingGraphStore()
    vector = _RecordingVectorStore(_vector_rows())
    events: list[tuple[str, dict[str, Any]]] = []

    done = _pipeline(profile, graph, vector).run(
        "seed",
        emit=lambda t, p: events.append((t, p)),
        generate=False,
        trace=True,
    )

    assert graph.expansion_calls == []
    assert vector.calls[0][1:] == (5, "it")
    assert ("status", {"stage": "graph", "enabled": True}) not in events
    graph_trace = next(event for event in done["trace"] if event.get("stage") == "graph")
    assert graph_trace["enabled"] is False
    assert graph_trace["skeleton_rows"] == []
    vector_trace = next(event for event in done["trace"] if event.get("stage") == "vector")
    assert [candidate["chunk_id"] for candidate in vector_trace["candidates"]] == [
        "chk:first",
        "chk:second",
    ]
    assert [
        source["source_url"]
        for source in done["sources"]
        if source["axis"] == "vector"
    ] == ["src://first", "src://second"]
    assert done["graph_degraded"] is False
    assert done["retrieval_time_s"] >= 0.0
    assert done["total_time_s"] >= done["retrieval_time_s"]


def test_pipeline_vector_results_seed_bounded_expansion() -> None:
    order: list[str] = []
    graph = _CountingGraphStore(events=order)
    vector = _RecordingVectorStore(_vector_rows(), events=order)
    events: list[tuple[str, dict[str, Any]]] = []

    done = _pipeline(PROFILE, graph, vector).run(
        "seed",
        emit=lambda t, p: events.append((t, p)),
        generate=False,
        trace=True,
    )

    assert order == ["vector", "expand"]
    assert graph.expansion_calls == [
        (
            ["tag:it:first", "tag:it:second"],
            {
                "direction": "parent",
                "kinds": None,
                "max_depth": 2,
                "max_fanout": 4,
                # ADR-071: до адаптера доходит бюджет надмножества (8 * 4), а не сам срез.
                "max_nodes": 32,
            },
        )
    ]
    assert ("status", {"stage": "graph", "enabled": True}) in events
    expansion_trace = next(
        event for event in done["trace"] if event.get("stage") == "graph_expansion"
    )
    assert expansion_trace["seed_chunk_ids"] == ["chk:first", "chk:second"]
    assert expansion_trace["context_ids"] == ["tag:it:first", "tag:it:second"]
    assert expansion_trace["paths"] == [["tag:it:first", "tag:it:parent"]]
    assert expansion_trace["depths"] == [1]
    vector_trace = next(event for event in done["trace"] if event.get("stage") == "vector")
    assert [candidate["chunk_id"] for candidate in vector_trace["candidates"]] == [
        "chk:first",
        "chk:second",
    ]
    graph_source = next(s for s in done["sources"] if s["source_url"] == "src://graph")
    # Обход дошёл до источника, но кандидата из него не получил: `relevance` есть, у него нет,
    # а метка говорит именно об этом (ADR-064). Раньше здесь стояло `1.0` и `contributed`,
    # то есть источник выглядел принесённым и высокорелевантным, хотя читать было нечего.
    assert (graph_source["relevance"], graph_source["relevance_basis"]) == (None, "none")
    assert graph_source["axis"] == "graph"
    assert graph_source["graph_knowledge"] == "reachable_unused"
    assert graph_source["graph_knowledge_reason"] == "no_candidate"
    assert done["graph_degraded"] is False


def test_pipeline_env_disabled_overrides_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = _CountingGraphStore()
    vector = _RecordingVectorStore(_vector_rows())
    monkeypatch.setenv("RETRIEVAL_GRAPH_ENABLED", "false")

    _pipeline(PROFILE, graph, vector).run("seed", generate=False)

    assert graph.expansion_calls == []
    assert graph.events == []


def test_pipeline_graph_failure_falls_back_to_vector_baseline() -> None:
    class FailingGraphStore(_CountingGraphStore):
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
            super().expand(
                context_ids,
                direction=direction,
                max_depth=max_depth,
                max_fanout=max_fanout,
                max_nodes=max_nodes,
            )
            raise RuntimeError("graph unavailable")

    graph = FailingGraphStore()
    vector = _RecordingVectorStore(_vector_rows())

    done = _pipeline(PROFILE, graph, vector).run("seed", generate=False, trace=True)

    assert done["graph_degraded"] is True
    assert [
        source["source_url"]
        for source in done["sources"]
        if source["axis"] == "vector"
    ] == ["src://first", "src://second"]
    degraded_trace = next(
        event for event in done["trace"] if event.get("stage") == "graph_expansion"
    )
    assert degraded_trace["degraded"] is True


def test_pipeline_shutdown_rejects_new_runs() -> None:
    pipe = _pipeline(PROFILE, _CountingGraphStore())
    pipe.shutdown()
    with pytest.raises(RuntimeError):
        pipe.run("как устроена база данных")


# --- Semantic Cache (бандл 3/3) ----------------------------------------


class _CountingLLM:
    """FakeLLM, считающий число вызовов generate и дельт (проверка: cache-hit не шлёт token)."""

    def __init__(self, text: str = "ответ") -> None:
        self._text = text
        self.call_count = 0
        self.token_count = 0

    def generate(
        self,
        prompt: str,
        system: str = "",
        stream: bool = True,
        labels: dict[str, str] | None = None,
    ):
        self.call_count += 1
        for delta in self._text.split(" "):
            self.token_count += 1
            yield delta


def _pipeline_with_cache(
    cache: InMemorySemanticCache | None = None,
) -> QueryPipeline:
    profile = {
        **PROFILE,
        "retrieval": {**PROFILE["retrieval"], "graph_search_enabled": False},
    }
    return QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=_CountingGraphStore(),
        vector_store=InMemoryVectorStore(),
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="ответ"),
        profile_loader=_StubLoader(profile),
        semantic_cache=cache,
    )


def test_cache_hit_returns_cached_answer_no_llm_no_token() -> None:
    """miss + повторный hit: LLM вызван 1 раз, tokenevents 0, cache_hit=True."""
    cache = InMemorySemanticCache(threshold=0.80, ttl_s=0)
    pipe = _pipeline_with_cache(cache)
    llm = _CountingLLM("ответ")

    # Patch llm into pipeline
    pipe._llm = llm

    # miss
    done_miss = pipe.run("как устроена база данных")
    assert done_miss.get("cache_hit") is False
    assert llm.call_count == 1

    # hit
    done_hit = pipe.run("как устроена база данных")
    assert done_hit["text"] == "ответ"
    assert done_hit["cache_hit"] is True
    assert done_hit["generation_time_s"] == 0.0
    assert done_hit["retrieval_time_s"] == 0.0
    assert done_hit["cache_lookup_s"] >= 0.0
    # LLM не вызывался повторно
    assert llm.call_count == 1
    # token не шлётся при hit
    assert done_hit.get("sources") is not None


def test_bridge_turns_reached_chunk_ids_into_readable_candidates() -> None:
    """Мост превращает `chunk_ids` строки обхода в кандидата С ТЕКСТОМ.

    Ключевая деталь теста: кандидат, которого достиг обход, **нет** среди строк, которые
    отдаёт векторный поиск. Иначе тест проходил бы и без моста — вектор вернул бы этот
    чанк сам, и проверка ничего бы не сторожила.

    Текст берётся из НАСТОЯЩЕГО хранилища через `upsert_vectors`, а не из подставного
    метода: подставной метод проверит, что пайплайн вызвал то, что мы и так написали.
    """
    hidden = "текст чанка, которого векторный поиск не вернул"
    vector = _RecordingVectorStore(_vector_rows())
    # Связь собирается так же, как её собирает обход: `chunk_ids` строки расширения.
    vector.upsert_vectors(
        [
            {
                "chunk_id": "chk:graph",
                "embedding": [0.1],
                "text": hidden,
                "metadata": {"source_url": "src://first", "domain": "it"},
            }
        ]
    )
    graph = _CountingGraphStore(expansion_rows=_expansion_rows_reaching("chk:graph"))
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=graph,
        vector_store=vector,
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="да"),
        profile_loader=_StubLoader(PROFILE),
    )

    done = pipe.run("seed", generate=False)

    assert done["effective_retrieval"]["bridge_status"] == "ok"
    assert done["effective_retrieval"]["bridged_chunks"] == 1
    # Тело выросло ровно на один фрагмент: вектор отдаёт два чанка, мост добавляет третий.
    # Если бы мост не сработал, `body_count` остался бы равен двум.
    assert done["context"]["body_count"] == len(_vector_rows()) + 1


def _expansion_rows_reaching(chunk_id: str) -> list[dict[str, Any]]:
    """Строка расширения, чей `chunk_ids` указывает на недоступный вектору чанк."""
    return [
        {
            "node_id": "tag:it:first",
            "canonical_name": "First",
            "path": ["tag:it:first", "tag:it:neighbour"],
            "depth": 1,
            "kind": "related",
            "origin": "user",
            "confidence": 0.8,
            "source_ids": ["src://first"],
            "chunk_ids": [chunk_id],
            "domain": "it",
        }
    ]


def test_bridge_reports_unsupported_store_apart_from_empty_result() -> None:
    """`None` и `[]` — разные вещи, и обе обязаны быть видны.

    Если хранилище не умеет читать по идентификаторам, отчёт обязан сказать «возможности
    нет», а не «прочитанного нет» и уж тем более не «граф не дал». Слив эти состояния
    даёт молчащий ноль, который читается как результат.
    """
    graph = _CountingGraphStore(expansion_rows=_expansion_rows_reaching("chk:missing"))
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=graph,
        vector_store=_NoBridgeStore(_vector_rows()),
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="да"),
        profile_loader=_StubLoader(PROFILE),
    )

    done = pipe.run("seed", generate=False)

    assert done["effective_retrieval"]["bridge_status"] == "unsupported"
    assert done["effective_retrieval"]["bridged_chunks"] == 0


class _NoBridgeStore(InMemoryVectorStore):
    """Хранилище, у которого возможности чтения по идентификаторам нет.

    Заглушка вынуждена: сегодня возможность есть у обоих хранилищ, и «нет возможности»
    нельзя получить иначе. Возвращается именно `None`, а не пустой список — в этом весь
    смысл проверки.
    """

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        super().__init__()
        self._rows = rows

    def vector_search(
        self,
        embedding: list[float],
        top_k: int = 5,
        domain: str | None = None,
    ) -> list[dict[str, Any]]:
        del embedding, domain
        return [dict(row) for row in self._rows[:top_k]]

    def get_chunks_by_ids(
        self,
        chunk_ids: Sequence[str],
        domain: str | None = None,
    ) -> list[dict[str, Any]] | None:
        del chunk_ids, domain
        return None


def test_bridge_does_not_invent_a_score_for_graph_candidates() -> None:
    """Мост не выдаёт оценку: придуманная константа повторила бы старый вред.

    Проверяется ровно то, что мост производит, — чанк уходит на ранжирование **без
    собственной оценки**. Оценку назначает плагин смешения, и пока его нет кандидат
    обязан уходить вниз.

    Проверка намеренно НЕ смотрит на `relevance` источника: эту константу ставит
    `build_sources` по оси графа, и она относится к задаче плагина, а не к мосту.
    Смешивать здесь две разные правки значит написать проверку под ещё не принятое
    поведение.
    """
    vector = _RecordingVectorStore(_vector_rows())
    vector.upsert_vectors(
        [
            {
                "chunk_id": "chk:graph",
                "embedding": [0.1],
                "text": "текст достигнутого обходом чанка",
                "metadata": {"source_url": "src://first", "domain": "it"},
            }
        ]
    )
    reranker = _ChunkRecordingReranker()
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=_CountingGraphStore(expansion_rows=_expansion_rows_reaching("chk:graph")),
        vector_store=vector,
        reranker=reranker,
        llm=FakeLLM(text="да"),
        profile_loader=_StubLoader(PROFILE),
    )

    pipe.run("seed", generate=False)

    bridged = [chunk for chunk in reranker.seen if chunk.get("chunk_id") == "chk:graph"]
    # Кандидат дошёл до ранжирования — иначе проверка ниже прошла бы на пустом списке.
    assert bridged, "мост не передал достигнутый чанк на ранжирование"
    assert bridged[0].get("bridged") is True
    assert not bridged[0].get("score"), (
        "мост выдал оценку графическому кандидату; это константа вместо решения плагина"
    )
    # Векторные кандидаты оценку сохраняют: отсутствие оценки — свойство моста,
    # а не общее правило для всех чанков.
    scored = [chunk for chunk in reranker.seen if float(chunk.get("score") or 0.0) > 0.0]
    assert scored, "векторные кандидаты должны сохранять оценку"


class _ChunkRecordingReranker(Reranker):
    """Запоминает чанки в том виде, в каком их передали на ранжирование."""

    def __init__(self) -> None:
        self.seen: list[dict[str, Any]] = []

    def rerank(self, query: str, chunks: list[dict[str, Any]]) -> list[float]:
        del query
        self.seen = [dict(chunk) for chunk in chunks]
        return [float(chunk.get("score") or 0.0) for chunk in chunks]


def test_filter_demotes_uncorroborated_candidate_instead_of_dropping_it() -> None:
    """Не подтверждённый пересечением кандидат ПОНИЖАЕТСЯ, а не выбрасывается.

    Проверяются обе части решения: кандидат остаётся в выдаче, его оценка уменьшилась,
    и сам факт отклонения записан в артефакт вместе с именем кандидата. Если бы падение
    было молчащим, отчёт показал бы меньше источников без следа - ровно тот отказ, из-за
    которого в проекте появилось правило о молчании.
    """
    recorder = _ChunkRecordingReranker()
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=_CountingGraphStore(expansion_rows=_expansion_rows_reaching("chk:graph")),
        vector_store=_RecordingVectorStore(_vector_rows()),
        reranker=recorder,
        llm=FakeLLM(text="да"),
        profile_loader=_StubLoader(PROFILE),
    )

    # Вопрос НЕ упоминает `First`, поэтому ни одна достигнутая сущность не подтверждена.
    done = pipe.run("совсем другой вопрос", generate=False)

    effective = done["effective_retrieval"]
    assert effective["filter_status"] == "no_corroborated_entities"
    # Ничего не отклонено, потому что отбирать было не по чему. Это не «фильтр сработал
    # и никого не нашёл» - разные утверждения, и путать их нельзя.
    assert effective["filter_demoted"] == 0

    pipe_filtering = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=_CountingGraphStore(expansion_rows=_expansion_rows_reaching("chk:first")),
        vector_store=_RecordingVectorStore(_vector_rows()),
        reranker=recorder,
        llm=FakeLLM(text="да"),
        profile_loader=_StubLoader(PROFILE),
    )
    filtered = pipe_filtering.run("расскажи про First", generate=False)

    effective = filtered["effective_retrieval"]
    assert effective["filter_status"] == "applied"
    demoted = effective["filter_demoted_candidates"]
    assert demoted, "не подтверждённый кандидат должен быть записан в артефакт"
    for entry in demoted:
        assert entry["chunk_id"], "отклонённый кандидат обязан быть опознан"

    # Ключевое: отклонённый кандидат ДОШЁЛ до ранжирования. Если бы он был выброшен,
    # его не было бы в списке - и проверка «понижен, а не выброшен» была бы пустой.
    flagged = [chunk for chunk in recorder.seen if chunk.get("filter_demoted")]
    assert flagged, "отклонённый кандидат выброшен вместо понижения"
    untouched = [chunk for chunk in recorder.seen if not chunk.get("filter_demoted")]
    assert untouched, "подтверждённый кандидат должен остаться нетронутым"
    # Понижение ровно в масштабе, который ПРИМЕНИЛСЯ, а не в константе кода: масштаб
    # приходит из профиля, и величина понижения обязана быть наблюдаемой.
    original = {str(row["chunk_id"]): float(row["score"]) for row in _vector_rows()}
    applied_scale = float(effective["filter_demote_scale"])
    for chunk in flagged:
        chunk_id = str(chunk["chunk_id"])
        assert chunk_id in original, "отклонённый кандидат должен быть векторным"
        assert float(chunk["score"]) == pytest.approx(original[chunk_id] * applied_scale)
    for chunk in untouched:
        assert float(chunk["score"]) == original[str(chunk["chunk_id"])]


def test_filter_records_phrase_names_instead_of_using_them() -> None:
    """Имя-фраза не участвует в отборе и попадает в счётчик.

    Ровно тот случай, который описан в спецификации: в `canonical_name` попала целая
    фраза. Если такое значение пошло бы в отбор, оно не подтвердило бы запрос - и
    кандидат был бы отклонён по причине, которой нет.
    """
    phrase = "это не имя, а целая фраза, попавшая в поле имени"
    rows = _expansion_rows_reaching("chk:first")
    rows[0]["canonical_name"] = phrase
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=_CountingGraphStore(expansion_rows=rows),
        vector_store=_RecordingVectorStore(_vector_rows()),
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="да"),
        profile_loader=_StubLoader(PROFILE),
    )

    done = pipe.run("расскажи про " + phrase, generate=False)

    effective = done["effective_retrieval"]
    assert effective["filter_phrase_names"] == 1
    # Фраза не подтвердила запрос, поэтому отбор не состоялся - и это видно по статусу,
    # а не выглядит как «фильтр отработал вхолостую».
    assert effective["filter_status"] == "no_corroborated_entities"


def test_filter_stays_inert_when_graph_is_off() -> None:
    """При выключенном графе фильтр не трогает ничего и говорит об этом прямо.

    Иначе «нет данных для отбора» выглядит как «все кандидаты не подтверждены», и
    отрицательный результат механизма читается как результат.
    """
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=_CountingGraphStore(expansion_rows=_expansion_rows_reaching("chk:first")),
        vector_store=_RecordingVectorStore(_vector_rows()),
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="да"),
        profile_loader=_StubLoader(
            {**PROFILE, "retrieval": {**PROFILE["retrieval"], "graph_search_enabled": False}}
        ),
    )

    done = pipe.run("расскажи про First", generate=False)

    effective = done["effective_retrieval"]
    assert effective["filter_status"] == "not_requested"
    assert effective["filter_demoted"] == 0


def test_filter_rejects_scale_that_would_turn_demotion_into_a_boost() -> None:
    """Масштаб вне (0, 1] отвергается: понижение не должно становиться подъёмом."""
    profile = {**PROFILE, "retrieval": {**PROFILE["retrieval"], "filter_demote_scale": 1.5}}
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=_CountingGraphStore(expansion_rows=_expansion_rows_reaching("chk:first")),
        vector_store=_RecordingVectorStore(_vector_rows()),
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="да"),
        profile_loader=_StubLoader(profile),
    )

    done = pipe.run("расскажи про другой вопрос", generate=False)

    assert done["effective_retrieval"]["filter_demote_scale"] == FILTER_DEMOTE_SCALE


def test_filter_scale_reports_whether_it_was_ever_calibrated() -> None:
    """Величина понижения едет в артефакте вместе со своим статусом.

    Без статуса число в артефакте читается как решение, а оно решением не является: его нечем
    было откалибровать без набора с известным порядком. Через полгода это место перечитают и
    примут 0.5 за измеренную величину — именно так и появляются числа без владельца.
    """
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=_CountingGraphStore(expansion_rows=_expansion_rows_reaching("chk:first")),
        vector_store=_RecordingVectorStore(_vector_rows()),
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="да"),
        profile_loader=_StubLoader(PROFILE),
    )

    effective = pipe.run("расскажи про First", generate=False)["effective_retrieval"]
    # В фикстуре профиль задаёт своё значение - оно помечено как заданное профилем, а не как
    # откалиброванное.
    assert effective["filter_demote_scale_status"] == "profile"

    default_pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=_CountingGraphStore(expansion_rows=_expansion_rows_reaching("chk:first")),
        vector_store=_RecordingVectorStore(_vector_rows()),
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="да"),
        profile_loader=_StubLoader({**PROFILE, "retrieval": {"graph_search_enabled": True}}),
    )
    default_effective = default_pipe.run("расскажи про First", generate=False)["effective_retrieval"]
    assert default_effective["filter_demote_scale"] == FILTER_DEMOTE_SCALE
    assert default_effective["filter_demote_scale_status"] == "uncalibrated_placeholder"


def test_evidence_limit_drops_block_but_keeps_source_attribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ручка измерения убирает блок из промпта и НЕ трогает атрибуцию источников.

    Это вторая рука измерения: всё, кроме строк свидетельства, остаётся прежним. Если
    ограничение задевало бы список источников, сравнение смешивало бы два изменения, а
    вывод «блок не нужен» опирался бы на сдвиг выдачи, а не на пустой блок.
    """
    monkeypatch.setenv("RETRIEVAL_EVIDENCE_MAX_LINES", "0")
    graph = _CountingGraphStore(
        expansion_rows=[
            {
                "node_id": "tag:it:first",
                "canonical_name": "First",
                "path": ["tag:it:first"],
                "depth": 0,
                "kind": "parent",
                "source_ids": ["src://first"],
                "domain": "it",
            }
        ]
    )
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=graph,
        vector_store=_RecordingVectorStore(_vector_rows()),
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="да"),
        profile_loader=_StubLoader(PROFILE),
    )

    done = pipe.run("seed", generate=False)

    ctx = done["context"]
    assert ctx["skeleton_count"] == 0
    assert ctx["skeleton_lines"] == []
    assert ctx["skeleton_tokens"] == 0
    # Доля пустого блока при непустом теле - это ИЗМЕРЕННЫЙ ноль, а не "неизвестно":
    # расход известен, он равен нулю. `None` здесь означал бы, что мы не смогли посчитать.
    assert ctx["skeleton_token_share"] == 0.0
    # Тело и атрибуция на месте: блок убран, а не выдача сломана.
    assert ctx["body_count"] > 0
    assert done["effective_retrieval"]["evidence_max_lines"] == 0
    assert any(src.get("axis") == "graph" for src in done["sources"])


def test_evidence_limit_absent_means_no_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Отсутствие ручки - это «без ограничения», а не «ноль строк»."""
    monkeypatch.delenv("RETRIEVAL_EVIDENCE_MAX_LINES", raising=False)
    graph = _CountingGraphStore(
        expansion_rows=[
            {
                "node_id": "tag:it:first",
                "canonical_name": "First",
                "path": ["tag:it:first"],
                "depth": 0,
                "kind": "parent",
                "source_ids": ["src://first"],
                "domain": "it",
            }
        ]
    )
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=graph,
        vector_store=_RecordingVectorStore(_vector_rows()),
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="да"),
        profile_loader=_StubLoader(PROFILE),
    )

    done = pipe.run("seed", generate=False)

    assert done["context"]["skeleton_count"] > 0
    assert done["effective_retrieval"]["evidence_max_lines"] is None


def test_evidence_limit_rejects_garbage_without_changing_behaviour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Негодное значение ручки не должно молча превращаться в «показать ноль строк».

    Иначе опечатка в переменной выглядит как результат измерения: блок исчезает, и
    артефакт честно описывает прогон, который никто не заказывал.
    """
    monkeypatch.setenv("RETRIEVAL_EVIDENCE_MAX_LINES", "не число")
    graph = _CountingGraphStore(
        expansion_rows=[
            {
                "node_id": "tag:it:first",
                "canonical_name": "First",
                "path": ["tag:it:first"],
                "depth": 0,
                "kind": "parent",
                "source_ids": ["src://first"],
                "domain": "it",
            }
        ]
    )
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=graph,
        vector_store=_RecordingVectorStore(_vector_rows()),
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="да"),
        profile_loader=_StubLoader(PROFILE),
    )

    done = pipe.run("seed", generate=False)

    assert done["context"]["skeleton_count"] > 0
    assert done["effective_retrieval"]["evidence_max_lines"] is None


def test_done_records_what_the_model_saw_of_the_graph_axis() -> None:
    """Артефакт должен отвечать не только «сколько строк», но и «что в них было».

    Вопрос «помогает ли ось графа» без содержимого блока не отвечается: по прогону видно
    число строк, а что именно модель получила — нет. Тест строит строки так же, как
    пайплайн: `m` всегда `None`, поэтому форма `узел ~[ТИП]~ сосед` невозможна.
    """
    graph = _CountingGraphStore(
        expansion_rows=[
            {
                "node_id": "tag:it:first",
                "canonical_name": "First",
                "path": ["tag:it:first"],
                "depth": 0,
                "kind": "parent",
                "source_ids": ["src://first"],
                "domain": "it",
            }
        ]
    )
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=graph,
        vector_store=_RecordingVectorStore(_vector_rows()),
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="да"),
        profile_loader=_StubLoader(PROFILE),
    )

    done = pipe.run("seed", generate=False)

    lines = done["context"]["skeleton_lines"]
    assert lines, "обход отработал, а содержимое блока в ответе отсутствует"
    assert all("~[" not in line for line in lines), (
        "в блок попала форма, которую пайплайн собрать не может"
    )
    # Содержимое обязано быть именно тем, что ушло в промпт, иначе артефакт описывает
    # не тот ответ, который был дан.
    assert len(lines) == done["context"]["skeleton_count"]


def test_done_splits_context_budget_between_skeleton_and_body() -> None:
    """Разбиение бюджета видно в ответе, а не только в runtime-трейсе.

    Скелет входит первым и не вытесняется, тело получает остаток. Пока в ответе есть
    только суммарное число токенов, два разных вреда неразличимы: «граф вытеснил текст»
    и «граф занял своё место». Поэтому проверяем и раздельные величины, и их сумму.
    """
    graph = _CountingGraphStore(
        expansion_rows=[
            {
                "node_id": "tag:it:first",
                "canonical_name": "First",
                "path": ["tag:it:first"],
                "depth": 0,
                "kind": "parent",
                "source_ids": ["src://first"],
                "domain": "it",
            }
        ]
    )
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=graph,
        vector_store=_RecordingVectorStore(_vector_rows()),
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="да"),
        profile_loader=_StubLoader(PROFILE),
    )

    done = pipe.run("seed", generate=False)

    ctx = done["context"]
    assert ctx["skeleton_count"] > 0, "обход дал строки, а скелет в ответ не попал"
    assert ctx["max_tokens"] == PROFILE["context_assembly"]["max_tokens"]
    assert ctx["skeleton_tokens"] + ctx["body_tokens"] == ctx["total_tokens"]
    assert ctx["total_tokens"] <= ctx["max_tokens"]
    assert ctx["skeleton_token_share"] == pytest.approx(
        ctx["skeleton_tokens"] / ctx["total_tokens"], abs=1e-4
    )
    # Тело обязано что-то содержать: иначе «скелет занял весь бюджет» и «сборка не
    # выполнилась» выглядели бы одинаково.
    assert ctx["body_count"] > 0


def test_cache_hit_reports_context_budget_as_unknown_not_zero() -> None:
    """На кэш-попадании расход бюджета НЕИЗВЕСТЕН, а не нулевой.

    Кэш отдаёт ранее собранный ответ, и в этом прогоне контекст не собирался. Ноль
    означал бы «скелет и тело ничего не заняли», то есть молчаливое утверждение о том,
    чего не измеряли.
    """
    cache = InMemorySemanticCache(threshold=0.80, ttl_s=0)
    pipe = _pipeline_with_cache(cache)

    done_miss = pipe.run("как устроена база данных")
    assert done_miss["context"] is not None
    assert done_miss["context"]["total_tokens"] >= 0

    done_hit = pipe.run("как устроена база данных")
    assert done_hit["cache_hit"] is True
    assert done_hit["context"] is None


def test_cache_miss_stores_answer() -> None:
    cache = InMemorySemanticCache(threshold=0.80, ttl_s=0)
    pipe = _pipeline_with_cache(cache)
    pipe.run("как устроена база данных")
    # после miss ответ должен быть в кэше
    assert cache.stats()["entries"] >= 1


def test_no_cache_backward_compat() -> None:
    """Pipeline без кэша не меняет поля done (backward-compat с M2/M3)."""
    pipe = QueryPipeline(
        embedder=DeterministicEmbedder(),
        graph_store=_CountingGraphStore(),
        vector_store=InMemoryVectorStore(),
        reranker=NoOpRerankerAdapter(),
        llm=FakeLLM(text="ответ"),
        profile_loader=_StubLoader(PROFILE),
    )
    done = pipe.run("как устроена база данных")
    assert "cache_hit" not in done


# --- Ревизия данных в query-контуре (ADR-026) ------------------------

def test_done_carries_revision_on_generate() -> None:
    cache = InMemorySemanticCache(threshold=0.80, ttl_s=0)
    pipe = _pipeline_with_cache(cache)
    done = pipe.run("как устроена база данных", revision="revA")
    assert done["revision"] == "revA"
    assert done["cache_hit"] is False


def test_done_carries_revision_on_hit() -> None:
    cache = InMemorySemanticCache(threshold=0.80, ttl_s=0)
    pipe = _pipeline_with_cache(cache)
    pipe.run("как устроена база данных", revision="revA")
    done_hit = pipe.run("как устроена база данных", revision="revA")
    assert done_hit["cache_hit"] is True
    assert done_hit["revision"] == "revA"


def test_done_revision_defaults_none() -> None:
    pipe = _pipeline_with_cache()
    done = pipe.run("как устроена база данных")
    assert done["revision"] is None


def test_epoch_bump_hit_only_same_revision() -> None:
    cache = InMemorySemanticCache(threshold=0.80, ttl_s=0)
    pipe = _pipeline_with_cache(cache)
    pipe.run("как устроена база данных", revision="revA")
    # bump: та же формулировка под новой ревизией -> miss -> полный цикл
    done_bump = pipe.run("как устроена база данных", revision="revB")
    assert done_bump["cache_hit"] is False
    assert done_bump["revision"] == "revB"


# --- Глубина обхода как параметр запроса (ADR-036) ------------------------------------------
#
# Проверяется не «прошло ли», а четыре вещи, на которых стоит контракт: запрос ВЫШЕ
# конфигурации, превышение потолка объявляется, отсутствие параметра оставляет цепочку
# конфигурации нетронутой, а негодное значение отсекается до работы. Молчаливый зажим
# здесь и есть главный риск: именно он сделал бы потолок незаметным.


def _depth_pipeline() -> tuple[QueryPipeline, _CountingGraphStore]:
    """Конвейер с настоящими сидами обхода: без них `expand` просто не вызывается."""
    graph = _CountingGraphStore()
    return (
        _pipeline(PROFILE, graph, vector_store=_RecordingVectorStore(_vector_rows())),
        graph,
    )


def test_absent_request_depth_leaves_config_chain_untouched() -> None:
    pipe, _ = _depth_pipeline()
    done = pipe.run("вопрос", domain="it", generate=False)

    assert done["effective_retrieval"]["max_depth"] == 2, "без параметра решает профиль"
    assert done["effective_retrieval"]["max_depth_requested"] == 2
    assert done["effective_retrieval"]["max_depth_effective"] == 2
    assert done["effective_retrieval"]["depth_clamped"] is False


def test_request_depth_overrides_profile_and_reaches_the_walk() -> None:
    pipe, graph = _depth_pipeline()
    done = pipe.run("вопрос", domain="it", generate=False, max_depth=4)

    # Запрос выше профиля: до обхода дошло именно запрошенное.
    assert graph.expansion_calls[0][1]["max_depth"] == 4
    assert done["effective_retrieval"]["max_depth"] == 2, "конфигурация остаётся конфигурацией"
    assert done["effective_retrieval"]["max_depth_requested"] == 4
    assert done["effective_retrieval"]["max_depth_effective"] == 4
    assert done["effective_retrieval"]["depth_clamped"] is False


def test_depth_permits_what_is_allowed_and_reports_what_was_walked() -> None:
    """ADR-059: разрешённая глубина и пройденная - разные величины.

    Пустой граф - не «граф не нужен», а ноль пройденных хопов. Счётчик, который на этом
    случае показывает разрешённое число, выглядит работающим и не измеряет ничего.
    """
    graph = _CountingGraphStore(expansion_rows=[])
    pipe = _pipeline(PROFILE, graph, vector_store=_RecordingVectorStore(_vector_rows()))

    done = pipe.run("вопрос", domain="it", generate=False, max_depth=3)

    effective = done["effective_retrieval"]
    assert effective["max_depth_requested"] == 3
    assert effective["max_depth_allowed"] == 3
    assert effective["hops_traversed"] == 0, "обход шёл и ничего не нашёл - честный ноль"
    assert effective["depth_clamped"] is False


def test_hops_traversed_counts_rows_that_were_actually_returned() -> None:
    """Сторож счётчика: ненулевой случай обязан отличаться от нулевого.

    Тест на пустом графе проходит и при откате счётчика в константу, потому что там ноль
    и так настоящий. Защищает только этот: строка с `depth=1` обязана дать 1, а не 0.
    """
    graph = _CountingGraphStore()  # строка с depth=1 по умолчанию
    pipe = _pipeline(PROFILE, graph, vector_store=_RecordingVectorStore(_vector_rows()))

    done = pipe.run("вопрос", domain="it", generate=False)

    assert done["effective_retrieval"]["hops_traversed"] == 1


def test_request_depth_above_cap_is_clamped_and_declared() -> None:
    """Просьба «дальше, чем готов обойти» - не ошибка запроса, но и не молчалка."""
    pipe, _ = _depth_pipeline()
    done = pipe.run("вопрос", domain="it", generate=False, max_depth=99)

    assert done["effective_retrieval"]["max_depth_requested"] == 99
    assert done["effective_retrieval"]["max_depth_effective"] == MAX_EXPANSION_DEPTH == 6
    assert done["effective_retrieval"]["depth_clamped"] is True


def test_cap_allows_documented_parthood_chain() -> None:
    """Потолок 6 обязан пропускать партономию: 5 хопов от дома до страны (docs/01 §2)."""
    pipe, _ = _depth_pipeline()
    done = pipe.run("вопрос", domain="it", generate=False, max_depth=5)

    assert done["effective_retrieval"]["max_depth_effective"] == 5
    assert done["effective_retrieval"]["depth_clamped"] is False


def test_request_depth_is_reported_in_trace() -> None:
    pipe, _ = _depth_pipeline()
    done = pipe.run("вопрос", domain="it", generate=False, max_depth=99, trace=True)

    expansion = [event for event in done["trace"] if event.get("stage") == "graph_expansion"]
    assert expansion, "событие graph_expansion обязано быть в трассировке"
    assert expansion[0]["requested_depth"] == 99
    assert expansion[0]["effective_depth"] == MAX_EXPANSION_DEPTH
    assert expansion[0]["depth_clamped"] is True


def test_invalid_request_depth_is_refused_before_any_work() -> None:
    """Ноль, отрицательное, дробное, строка и bool - разные поводы отказать."""
    pipe, graph = _depth_pipeline()
    for bad in (0, -1, 2.5, "3", True):
        with pytest.raises((TypeError, ValueError)):
            pipe.run("вопрос", domain="it", generate=False, max_depth=bad)  # type: ignore[arg-type]

    assert graph.expansion_calls == [], "негодная глубина обязана отсекаться до обхода"
