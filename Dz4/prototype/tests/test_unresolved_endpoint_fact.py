"""Неразрешённый конец связи — факт, а не повод снести LLM-слой (ADR-037, случай 3).

Раньше одна связь, концы которой не нашлись среди имён того же ответа, уничтожала весь
LLM-слой документа: `_validate_edges` поднимал `ExtractionModelError`, продовый путь сносил
`ctx.entities` и `ctx.entity_edges`. В прогоне expD это 10 сущностей и 5 связей, из которых
4 связи были в порядке, — и все они исчезли из-за одной.

Тест закрывает четыре решения, принятые в ADR-038, потому что они связаны: без (1) теряется
`kind`, без (2) джоба уборки не найдёт конец, без (3) обход графа упрётся в висящие концы,
а без (4) неразрешённость потеряла бы связь с ответом, из которого она взята.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import pytest

from graphrag_proto.ingestion_service.pipeline.orchestrator import (
    CAUSE_MODEL_ERROR,
    ExtractionModelError,
    ExtractStage,
    PipelineContext,
)
from tests.job_wait import wait_for_terminal

_ENTITIES = {
    "tags": [
        {"canonical_name": "REQ_INDEX", "name": "REQ_INDEX"},
        {"canonical_name": "REQ_STORE", "name": "REQ_STORE"},
    ],
}
_RESOLVABLE = {"from": "REQ_INDEX", "to": "REQ_STORE", "kind": "depends_on", "confidence": 0.8}
_UNRESOLVABLE = {"from": "REQ_INDEX", "to": "REQ_MADE_UP", "kind": "depends_on", "confidence": 0.9}


class _OneShotLLM:
    """Адаптер с тем же контрактом, что и боевой: `generate` отдаёт ИТЕРАТОМ кусков."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._text = json.dumps(payload, ensure_ascii=False)
        self.last_usage: dict[str, int] = {"prompt_tokens": 1, "completion_tokens": 1}

    def generate(self, *_args: Any, **_kwargs: Any) -> Any:
        return iter([self._text])


def _profile() -> dict[str, Any]:
    return {
        "profile": {"name": "it", "version": "1"},
        "ontology": {"node_types": [{"type": "Requirement"}]},
        "extraction": {
            "llm_enabled": True,
            "prompt_template": {
                "id": "t",
                "system": "s",
                "user": "u",
            },
        },
    }


def _ctx(chunks: list[str] | None = None) -> PipelineContext:
    return PipelineContext(
        job_id="j",
        domain="it",
        doc_type="txt",
        source_url="src://d.txt",
        chunks=chunks or ["индексировать и хранить документы"],
    )


def _run(payload: dict[str, Any], *, optional_failure: bool = True) -> PipelineContext:
    ctx = _ctx()
    ExtractStage(
        llm=_OneShotLLM(payload),
        profile_fetcher=lambda _domain: _profile(),
        optional_failure=optional_failure,
    ).run(ctx)
    return ctx


def test_unresolved_endpoint_does_not_drop_the_layer(monkeypatch: Any) -> None:
    """Главное: неразрешённый конец НЕ сносит слой. Раньше сносил."""
    monkeypatch.setenv("EXTRACT_LLM", "true")
    payload = deepcopy(_ENTITIES)
    payload["links"] = [_RESOLVABLE, _UNRESOLVABLE]

    ctx = _run(payload)

    assert ctx.enrichment_degraded is False
    assert ctx.llm_layer_dropped is False
    assert ctx.llm_layer_lost_entities == 0
    assert ctx.llm_layer_lost_edges == 0
    # Обе связи сохранены, а не одна: неразрешённость — свойство связи, а не повод её выбросить.
    assert len(ctx.entity_edges) == 2
    assert {entity["canonical"] for entity in ctx.entities} == {"REQ_INDEX", "REQ_STORE"}


def test_the_relation_row_survives_with_its_kind(monkeypatch: Any) -> None:
    """Строка связи сохраняется: `kind` и `confidence` — то, ради чего извлечение и делалось.

    Записать только факт означало бы выбросить `depends_on`, и тогда факт сказал бы «коня назвали
    REQ_MADE_UP», но не сказал бы, что это зависимость. Восстановить её нечем.
    """
    monkeypatch.setenv("EXTRACT_LLM", "true")
    payload = deepcopy(_ENTITIES)
    payload["links"] = [_UNRESOLVABLE]

    ctx = _run(payload)

    assert len(ctx.entity_edges) == 1
    edge = ctx.entity_edges[0]
    assert edge["kind"] == "depends_on"
    assert edge["confidence"] == 0.9
    assert edge["endpoint_unresolved"] is True
    assert edge["unresolved_sides"] == ["to"]
    # Помеченная связь обязана быть опознаваемой как неразрешённая, иначе обход по ней не
    # отличить от обычной.
    assert edge["to"] == "REQ_MADE_UP"


def test_the_fact_carries_the_name_as_the_model_wrote_it(monkeypatch: Any) -> None:
    """Факт несёт ИСХОДНОЕ имя, а не ключ идентичности.

    Ключ бесполезен для уборки: джобе надо сопоставить конец с текстом документа, а ключ
    уже потерял написание. Проверяются оба поля, потому что хранить только одно из них
    означало бы потерять либо сопоставление, либо право на склейку.
    """
    monkeypatch.setenv("EXTRACT_LLM", "true")
    payload = deepcopy(_ENTITIES)
    payload["links"] = [_UNRESOLVABLE]

    ctx = _run(payload)

    assert len(ctx.unresolved_endpoints) == 1
    fact = ctx.unresolved_endpoints[0]
    assert fact["endpoint_name"] == "REQ_MADE_UP"
    assert fact["endpoint_key"] == fact["endpoint_name"].casefold()
    assert fact["relation_kind"] == "depends_on"
    assert fact["other_endpoint_name"] == "REQ_INDEX"
    assert fact["chunk_id"]


def test_resolvable_only_answer_writes_nothing(monkeypatch: Any) -> None:
    """Нет неразрешённых концов — нет и фактов. Не «факт с пустым именем»."""
    monkeypatch.setenv("EXTRACT_LLM", "true")
    payload = deepcopy(_ENTITIES)
    payload["links"] = [_RESOLVABLE]

    ctx = _run(payload)

    assert ctx.unresolved_endpoints == []
    assert ctx.entity_edges[0].get("endpoint_unresolved") is None


def test_key_matches_after_case_and_spacing(monkeypatch: Any) -> None:
    """Ключ считается тем же `_identity_key`, что и у валидации, иначе разъедутся.

    Проверка против строки с другим регистром и лишними пробелами: если бы сопоставление шло
    по сырому тексту, «REQ_MADE_UP» и «  req_made_up » были бы разными концами, и факт
    записался бы дважды.
    """
    monkeypatch.setenv("EXTRACT_LLM", "true")
    payload = deepcopy(_ENTITIES)
    payload["links"] = [{"from": "REQ_INDEX", "to": "  req_made_up  ", "kind": "depends_on"}]

    ctx = _run(payload)

    assert len(ctx.unresolved_endpoints) == 1
    # Имя сохраняется как написано — с пробелами, — а ключ нормализован.
    assert ctx.unresolved_endpoints[0]["endpoint_name"].strip() == "req_made_up"
    assert ctx.unresolved_endpoints[0]["endpoint_key"] == "req_made_up"


def test_both_sides_unresolved_is_one_fact_each(monkeypatch: Any) -> None:
    """Оба конца неразрешённы — два факта, а не один и не молчание."""
    monkeypatch.setenv("EXTRACT_LLM", "true")
    payload = deepcopy(_ENTITIES)
    payload["links"] = [{"from": "NOPE_A", "to": "NOPE_B", "kind": "depends_on"}]

    ctx = _run(payload)

    assert {fact["endpoint_name"] for fact in ctx.unresolved_endpoints} == {"NOPE_A", "NOPE_B"}
    assert ctx.entity_edges[0]["unresolved_sides"] == ["from", "to"]


def test_malformed_answer_still_fails_loudly(monkeypatch: Any) -> None:
    """Негодная ФОРМА ответа по-прежнему поднимает ошибку.

    Пункт 3 касается разрешимости концов, а не JSON. Записать фактом «связь — это не список»
    нельзя: это не утверждение о домене, а дефект нашей границы с моделью, и притворяться
    фактом он не станет.
    """
    monkeypatch.setenv("EXTRACT_LLM", "true")
    for relations, message in (
        ("не список", "relationships должно быть списком"),
        ([{"from": "REQ_INDEX"}], "должен содержать from и to"),
        ([{"kind": "depends_on"}], "должен содержать from и to"),
    ):
        ctx = _ctx()
        payload = deepcopy(_ENTITIES)
        payload["links"] = relations
        with pytest.raises(ExtractionModelError, match=message):
            ExtractStage(
                llm=_OneShotLLM(payload),
                profile_fetcher=lambda _domain: _profile(),
            ).run(ctx)


def test_strict_mode_no_longer_rejects_the_document(monkeypatch: Any) -> None:
    """Строгий режим тоже не падает: снос слоя был поведением обоих режимов.

    Строгий режим (`optional_failure=False`) существует, чтобы ошибка всплывала наружу
    вместо деградации. Разрешимость концов — не ошибка, а факт, и всплывать тут нечему.
    """
    monkeypatch.setenv("EXTRACT_LLM", "true")
    payload = deepcopy(_ENTITIES)
    payload["links"] = [_UNRESOLVABLE]

    ctx = _ctx()
    ExtractStage(
        llm=_OneShotLLM(payload),
        profile_fetcher=lambda _domain: _profile(),
        optional_failure=False,
    ).run(ctx)

    assert ctx.enrichment_degraded is False
    assert len(ctx.entity_edges) == 1
    assert ctx.enrichment_cause != CAUSE_MODEL_ERROR


def test_optional_failure_keeps_layer_when_only_an_endpoint_is_unresolved(
    monkeypatch: Any,
) -> None:
    """Продовый путь (`optional_failure=True`) с неразрешённым концом: слой цел.

    Отдельный тест, потому что именно этот путь раньше сносил слой: `_validate_edges`
    поднимал `ExtractionModelError`, `_degrade_after_extraction_failure` чистил
    `ctx.entities` и `ctx.entity_edges`, и в прогоне expD так исчезали 10 сущностей и
    5 связей, 4 из которых были в порядке.

    Закреплено на ВТОРОМ чанке, где потеря была бы измерима: записи первого чанка уже в
    контексте, и раньше именно они исчезали.
    """
    monkeypatch.setenv("EXTRACT_LLM", "true")
    good = {**_ENTITIES, "links": [_RESOLVABLE]}
    bad = {**_ENTITIES, "links": [_UNRESOLVABLE]}
    ctx = _ctx(["индексировать и хранить", "индексировать и хранить документы"])

    ExtractStage(
        llm=_PerChunkLLM([json.dumps(good, ensure_ascii=False), json.dumps(bad, ensure_ascii=False)]),
        profile_fetcher=lambda _domain: _profile(),
        optional_failure=True,
    ).run(ctx)

    assert ctx.enrichment_degraded is False
    assert ctx.llm_layer_dropped is False
    assert ctx.llm_layer_lost_entities == 0
    assert ctx.llm_layer_lost_edges == 0
    # Обе связи на месте: первая разрешилась, вторая помечена как неразрешённая.
    assert len(ctx.entity_edges) == 2
    assert [edge.get("endpoint_unresolved", False) for edge in ctx.entity_edges] == [False, True]


class _PerChunkLLM:
    """Отвечает своим текстом на каждый чанк: сбой на втором чанке виден отдельно."""

    def __init__(self, texts: list[str]) -> None:
        self._texts = list(texts)
        self.last_usage: dict[str, int] = {}

    def generate(self, *_args: Any, **_kwargs: Any) -> Any:
        return iter([self._texts.pop(0)])


def test_the_storage_keeps_name_and_key_side_by_side(tmp_path: Any) -> None:
    """Таблица хранит и имя как написано, и ключ: ни одного из двух не хватает поодиночке.

    Проверяется на реальном `JobStore`, потому что потеря одного из полей — это не ошибка
    вычисления, а ошибка записи, и её видно только в базе: джоба уборки ищет по `endpoint_key`,
    а сопоставляет с текстом по `endpoint_name`.
    """
    from graphrag_proto.ingestion_service.storage.registry import JobStore

    store = JobStore(tmp_path / "jobs.db")
    try:
        store.create("j", "src://d.txt", "it", "txt")
        store.record_missing_endpoints(
            "j",
            "src://d.txt",
            [
                {
                    "chunk_id": "chk:1",
                    "endpoint_name": "  REQ_Made_Up ",
                    "endpoint_key": "req_made_up",
                    "relation_kind": "depends_on",
                    "other_endpoint_name": "REQ_INDEX",
                }
            ],
        )
        facts = store.missing_endpoints("j")
        assert len(facts) == 1
        assert facts[0]["endpoint_name"] == "  REQ_Made_Up "
        assert facts[0]["endpoint_key"] == "req_made_up"
        assert facts[0]["relation_kind"] == "depends_on"
        assert facts[0]["other_endpoint_name"] == "REQ_INDEX"
        # Пустой список не создаёт строк: «не было неразрешённых» и «не смотрели» —
        # разные утверждения, и молчание здесь было бы вторым.
        store.record_missing_endpoints("j2", "src://e.txt", [])
        assert store.missing_endpoints("j2") == []
    finally:
        store.close()


def test_unresolved_endpoints_reach_the_job_report(monkeypatch: Any, tmp_path: Any) -> None:
    """Факт должен попасть в отчёт джобы, иначе он записан в никуда.

    Именно этот разрыв не ловится тестами контекста: `ctx.unresolved_endpoints` наполнился бы,
    все проверки `_validate_edges` остались бы зелёными, а в базе было бы пусто — и джоба уборки
    не увидела бы ни одного кандидата. Запись факта делается в `app.py`, а не в стадии,
    поэтому проверять надо путь приложения целиком.
    """
    from graphrag_proto.ingestion_service.app import Executor
    from graphrag_proto.ingestion_service.storage.registry import DocumentRegistry, JobStore
    from graphrag_proto.retrieval.adapters.inmemory import (
        InMemoryGraphStore,
        InMemoryVectorStore,
    )
    from graphrag_proto.retrieval.adapters.llm import FakeLLM

    monkeypatch.setenv("EXTRACT_LLM", "true")
    jobs = JobStore(tmp_path / "missing-endpoints.db")
    registry = DocumentRegistry(tmp_path / "missing-endpoints-registry.db")
    source = tmp_path / "d.txt"
    source.write_text("требование индексировать документы", encoding="utf-8")

    payload = json.dumps({**_ENTITIES, "links": [_UNRESOLVABLE]}, ensure_ascii=False)
    executor = Executor(
        jobs,
        registry,
        glossary_url="",
        graph_store=InMemoryGraphStore(),
        vector_store=InMemoryVectorStore(),
        llm=FakeLLM(text=payload, is_fake=False),
        profile_fetcher=lambda _domain: _profile(),
    )
    jobs.create("job", "src://d.txt", "it", "txt")
    assert executor.start("job", source, "src://d.txt", "it", "txt") is True

    state, elapsed = wait_for_terminal(lambda: jobs.get("job"))
    failed = [s for s in jobs.stages("job") if s["status"] == "failed"]
    assert state["status"] == "succeeded", {
        "waited_s": round(elapsed, 1),
        "job": state,
        "failed_stages": failed,
    }

    facts = jobs.missing_endpoints("job")
    assert [fact["endpoint_name"] for fact in facts] == ["REQ_MADE_UP"]
    assert facts[0]["endpoint_key"] == "req_made_up"
    assert facts[0]["relation_kind"] == "depends_on"
    # Слой не потерян: иначе отчёт говорил бы «деградация», а потерян один конец.
    assert "llm_layer_dropped" not in jobs.signals("job")


def test_stage_is_not_degraded_so_the_cause_is_absent(monkeypatch: Any) -> None:
    """Неразрешённый конец не должен выглядеть как деградация в отчёте джобы.

    Смешивать их нельзя: деградация означает «слой потерян», а здесь слой цел и потерян один
    конец. Если бы причина осталась `model_error`, потребитель читал бы отчёт о сбое модели
    там, где модель ответила по существу правильно.
    """
    monkeypatch.setenv("EXTRACT_LLM", "true")
    payload = deepcopy(_ENTITIES)
    payload["links"] = [_UNRESOLVABLE]

    ctx = _run(payload)

    assert ctx.enrichment_degraded is False
    assert ctx.enrichment_cause is None
    assert ctx.llm_layer_dropped is False