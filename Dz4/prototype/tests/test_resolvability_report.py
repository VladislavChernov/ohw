"""Счётчики разрешимости в отчёте джобы (ADR-039).

Начиналось как «отчёт о загрузке» — отдельная сущность с агрегатом по документам. Решением
владельца сущность отменена: загрузка — это и есть ингест одного документа, а агрегат по джобам
есть запрос. Осталось то, что действительно нужно: числа, из которых доля вычисляется, и
знаменатели, без которых она не вычисляется.

Контрольный пример, заданный владельцем: `README` из `learning/`, загруженный первым в пустую
базу, даёт около 90% неразрешённых концов, и это **правильное** поведение — список ссылается на
методички, которых в базе ещё нет. Значит порога быть не может, варнинга быть не может, а
отсутствие данных обязано отличаться от нуля.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _llm_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Без этого флага `ExtractStage` не идёт в LLM-путь, и все счётчики остаются пустыми.

    Проверка стоит автоприменяемой намеренно: забытый флаг даёт не ошибку, а пустые числа,
    которые читаются как «считали, ничего не нашли».
    """
    monkeypatch.setenv("EXTRACT_LLM", "true")

_ENTITIES = {
    "tags": [
        {"canonical_name": "REQ_INDEX", "name": "REQ_INDEX"},
        {"canonical_name": "REQ_STORE", "name": "REQ_STORE"},
    ],
}
_RELATION = {"from": "REQ_INDEX", "to": "REQ_STORE", "kind": "depends_on", "confidence": 0.8}


def _profile() -> dict[str, Any]:
    return {
        "profile": {"name": "it", "version": "1"},
        "ontology": {"node_types": [{"type": "Requirement"}]},
        "extraction": {
            "llm_enabled": True,
            "prompt_template": {"id": "t", "system": "s", "user": "u"},
        },
    }


def _ctx(chunks: list[str]) -> Any:
    from graphrag_proto.ingestion_service.pipeline.orchestrator import PipelineContext

    return PipelineContext(
        job_id="j",
        domain="it",
        doc_type="txt",
        source_url="src://d.txt",
        chunks=chunks,
    )


def _run(payload: dict[str, Any], chunks: list[str]) -> Any:
    from graphrag_proto.ingestion_service.pipeline.orchestrator import ExtractStage

    class _LLM:
        def __init__(self) -> None:
            self.last_usage: dict[str, int] = {}

        def generate(self, *_args: Any, **_kwargs: Any) -> Any:
            return iter([json.dumps(payload, ensure_ascii=False)])

    ctx = _ctx(chunks)
    ExtractStage(
        llm=_LLM(),
        profile_fetcher=lambda _domain: _profile(),
        optional_failure=True,
    ).run(ctx)
    return ctx


def test_counters_are_denominators_not_just_a_rate() -> None:
    """Считаются слоты концов, и рядом лежит знаменатель.

    Считать по связям удваивало долю: у связи два конца, в expA все шесть потеряли оба.
    Числитель при этом был верен, то есть ошибка пережила пересчёт.
    """
    ctx = _run({**_ENTITIES, "links": [_RELATION]}, ["индексировать и хранить"])
    stats = ctx.resolvability
    assert stats["endpoints_total"] == 2
    assert stats["endpoints_resolved"] == 2
    assert stats["endpoints_unresolved"] == 0
    assert stats["relations"] == 1
    assert stats["endpoints_total"] == stats["relations"] * 2
    # Знаменатели обязаны быть: без них доля не вычисляется, а «концов нет» и «не считали» —
    # разные утверждения.
    assert stats["chunks"] == 1
    assert stats["entities_declared"] == 2
    assert stats["entities_distinct"] == 2


def test_unresolved_counts_are_split_into_slots_and_names() -> None:
    """Слоты и имена — разные величины, и агрегат по джобам суммирует слоты.

    Один и тот же конец встречается в нескольких слотах: expA давал 12 и 7. Смешать их
    означало бы либо завысить число фактов, либо потерять его.
    """
    relation = {"from": "REQ_INDEX", "to": "REQ_STORE", "kind": "depends_on"}
    broken = {"from": "REQ_INDEX", "to": "MADE_UP", "kind": "depends_on"}
    ctx = _run({**_ENTITIES, "links": [relation, broken, broken]}, ["индексировать и хранить"])
    stats = ctx.resolvability
    assert stats["endpoints_total"] == 6
    assert stats["endpoints_resolved"] == 4
    assert stats["endpoints_unresolved"] == 2
    # Одно имя, два слота.
    assert stats["names_unresolved"] == 1
    assert stats["endpoints_distinct"] == 3


def test_high_unresolved_share_is_reported_not_hidden() -> None:
    """Массовая неразрешённость обязана быть видна в отчёте, а не сглажена.

    Сценарий владельца: `README` из `learning` первым в пустую базу — около 90% концов
    не находят соответствия, и это правильное поведение. Если бы неразрешённые концы влияли
    на признак деградации, такой прогон выглядел бы поломкой извлечения.
    """
    links = [
        {"from": f"REQ_{index}", "to": "MISSING", "kind": "depends_on"} for index in range(9)
    ]
    ctx = _run({**_ENTITIES, "links": links}, ["индексировать и хранить"])
    stats = ctx.resolvability
    assert stats["endpoints_unresolved"] == 18
    assert stats["endpoints_resolved"] == 0
    assert ctx.enrichment_degraded is False
    assert ctx.llm_layer_dropped is False


def test_unresolved_names_attributed_to_the_field_they_came_from() -> None:
    """Из какого поля взят неразрешённый конец — измеримый факт, а не догадка.

    Два разных механизма дают одно и то же число: expA — концы из `id`, expD — из `category`.
    Одно число их не различает, а ремонт у них разный.
    """
    payload = deepcopy(_ENTITIES)
    payload["tags"] = [
        {"canonical_name": "REQ_INDEX", "name": "REQ_INDEX", "id": "INVENTED"},
        {"canonical_name": "REQ_STORE", "name": "REQ_STORE", "category": "storage things"},
    ]
    links = [
        {"from": "REQ_INDEX", "to": "INVENTED", "kind": "depends_on"},
        {"from": "REQ_STORE", "to": "storage things", "kind": "depends_on"},
    ]
    ctx = _run({**payload, "links": links}, ["индексировать и хранить"])
    assert ctx.resolvability["by_field"] == {"category": 1, "id": 1}


def test_structure_counters_are_measured_not_gated() -> None:
    """Петли, взаимные пары и входящая связность считаются, но дефектом не объявляются.

    Порог по входящей связности выбирается после того, как увидели числа, а это критерий по
    итогу — то же, что запрещено ADR-037 для хабов.
    """
    ctx = _run(
        {
            **_ENTITIES,
            "links": [
                {"from": "REQ_INDEX", "to": "REQ_INDEX", "kind": "depends_on"},
                {"from": "REQ_INDEX", "to": "REQ_STORE", "kind": "depends_on"},
                {"from": "REQ_STORE", "to": "REQ_INDEX", "kind": "depends_on"},
            ],
        },
        ["индексировать и хранить"],
    )
    stats = ctx.resolvability
    assert stats["self_loops"] == 1
    assert stats["mutual_pairs"] == 1
    assert stats["max_fan_in"] == 2
    assert ctx.enrichment_degraded is False


def test_distinct_counters_are_per_document_not_a_sum_over_chunks(
) -> None:
    """«Уникальные» считаются один раз на документ, а не складываются по чанкам.

    Имя, встретившееся в двух чанках, при суммировании посчитано дважды. Ошибка уехала бы
    ровно туда, где документ длиннее: чем больше чанков, тем больше «уникальных» имён при
    том же их числе на самом деле.
    """
    ctx = _run(
        {**_ENTITIES, "links": [_RELATION, _RELATION]},
        ["индексировать и хранить", "индексировать и хранить документы"],
    )
    stats = ctx.resolvability
    assert stats["chunks"] == 2
    assert stats["entities_declared"] == 4
    assert stats["entities_distinct"] == 2, "одни и те же две сущности на двух чанках"
    # Слоты суммируются: две связи на каждом из двух чанков дают 8 концов по два слота,
# а уникальных концов два — те же самые, что и в первом чанке.
    assert stats["endpoints_total"] == 8
    assert stats["endpoints_distinct"] == 2


def test_the_report_survives_a_round_trip_through_the_database(tmp_path: Any) -> None:
    """Счётчики обязаны читаться обратно из базы, а не существовать только в контексте.

    Проверяется на реальном `JobStore`: потеря столбца при записи или чтении — это ошибка
    хранения, и в тестах контекста её не видно.
    """
    from graphrag_proto.ingestion_service.storage.registry import JobStore

    ctx = _run({**_ENTITIES, "links": [_RELATION]}, ["индексировать и хранить"])
    store = JobStore(tmp_path / "jobs.db")
    try:
        store.create("j", "src://d.txt", "it", "txt")
        store.record_resolvability("j", ctx.resolvability, stage="EXTRACT")
        stats = store.resolvability("j")
        assert stats["measured_in_stage"] == "EXTRACT"
        # Всё, кроме отметки о стадии, обязано пережить поход в базу без потерь.
        assert {key: value for key, value in stats.items() if key != "measured_in_stage"} == {
            **ctx.resolvability,
            "measured": 1,
        }
        assert stats["measured"] == 1
        # Другая джоба: счётчиков нет — и это разные утверждения, а не нули.
        store.create("j2", "src://e.txt", "it", "txt")
        assert store.resolvability("j2") == {}
    finally:
        store.close()


def test_the_stage_where_it_was_measured_is_recorded(tmp_path: Any) -> None:
    """Стадия, в которой мерили, пишется явно — и не называется «завершена».

    Имя `stage_completed` утверждало бы, что стадия дошла до конца, а это другое: при
    прерванной стадии счётчики могли собраться на неполном наборе чанков. По строке должно
    быть видно, где именно производилось измерение, не читая `jobs.status` — статус живёт в
    другой таблице и меняется позже.
    """
    from graphrag_proto.ingestion_service.storage.registry import JobStore

    store = JobStore(tmp_path / "jobs.db")
    try:
        store.create("j", "src://d.txt", "it", "txt")
        store.record_resolvability("j", {"endpoints_total": 2}, stage="EXTRACT")
        stats = store.resolvability("j")
        assert stats["measured_in_stage"] == "EXTRACT"
    finally:
        store.close()


def test_no_stage_is_null_not_a_guess(tmp_path: Any) -> None:
    """Без указания стадии — `null`, а не догадка о последней.

    Иначе колонка врала бы там, где её не заполнили: «EXTRACT» выглядело бы как
    подтверждённое измерение.
    """
    from graphrag_proto.ingestion_service.storage.registry import JobStore

    store = JobStore(tmp_path / "jobs.db")
    try:
        store.create("j", "src://d.txt", "it", "txt")
        store.record_resolvability("j", {"endpoints_total": 2})
        assert store.resolvability("j")["measured_in_stage"] is None
    finally:
        store.close()


def test_by_field_survives_storage_as_a_mapping(tmp_path: Any) -> None:
    """Распределение по полям обязано пережить JSON-сериализацию как словарь.

    Хранится оно в одном столбце `by_field_json`, потому что набор полей задаёт модель ответа,
    а не схема; столбец на каждое возможное поле означал бы `ALTER` при каждом новом.
    """
    from graphrag_proto.ingestion_service.storage.registry import JobStore

    store = JobStore(tmp_path / "jobs.db")
    try:
        store.create("j", "src://d.txt", "it", "txt")
        store.record_resolvability("j", {"by_field": {"id": 3, "category": 1}})
        assert store.resolvability("j")["by_field"] == {"id": 3, "category": 1}
    finally:
        store.close()


def test_measured_flag_separates_zero_from_not_measured(tmp_path: Any) -> None:
    """`measured` отвечает на вопрос «считал ли кто-нибудь», а не «что насчитали».

    При отсутствии измерения ноль показал бы «дефекта не было» там, где на самом деле ничего
    не считали. Это тот же класс, что и `unknown` у прибора.
    """
    from graphrag_proto.ingestion_service.storage.registry import JobStore

    store = JobStore(tmp_path / "jobs.db")
    try:
        store.create("j", "src://d.txt", "it", "txt")
        store.record_resolvability("j", {"endpoints_total": 0, "measured": 0})
        stats = store.resolvability("j")
        assert stats["measured"] == 0
        assert stats["endpoints_total"] == 0
    finally:
        store.close()