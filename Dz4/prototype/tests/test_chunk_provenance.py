"""Сущность, названная в двух чанках, теряет привязку ко второму (ADR-058).

Измерено на живом стенде, документ `docs/04_services_config.md`: имя `config service`
модель назвала в чанках `chk:dc757f3ab957` и `chk:5298dc6b401e`, а в Neo4j у ноды
`chunk_ids = ["chk:dc757f3ab957"]` — один. Потерян не сам факт существования сущности,
а место, где она встречается, а по нему потом строится выдача `sources`.

Тест проверяет положительный контракт: нода, собранная из записей разных чанков, обязана
нести оба `chunk_id`. Отрицательная формулировка («второй чанк не потерян») была бы
хрупкой: она ломалась бы от любой законной переработки слияния.
"""

from __future__ import annotations

from graphrag_proto.ingestion_service.pipeline.orchestrator import (
    DedupStage,
    NormalizeStage,
    PipelineContext,
    _entity_record,
)


def _record(ctx: PipelineContext, name: str, chunk_id: str) -> dict:
    return _entity_record(
        "Concept",
        {"canonical_name": name, "name": name},
        ctx,
        chunk_id,
        "llm:test",
    )


def _ctx() -> PipelineContext:
    return PipelineContext(
        job_id="job-1",
        source_url="docs/x.md",
        domain="it",
        doc_type="md",
    )


def test_node_named_in_two_chunks_keeps_both_chunk_ids() -> None:
    """Одно имя в двух чанках - одна нода, но привязка к обоим чанкам обязана сохраниться."""
    ctx = _ctx()
    first = _record(ctx, "Config Service", "chk:aaa")
    second = _record(ctx, "Config Service", "chk:bbb")
    ctx.entities = [first, second]

    DedupStage().run(ctx)

    assert len(ctx.entities) == 1, "одно имя в двух чанках должно дать одну ноду"
    node = ctx.entities[0]
    assert set(node["chunk_ids"]) == {"chk:aaa", "chk:bbb"}, (
        "привязка ко второму чанку потеряна: именно по chunk_ids строится provenance"
    )


def test_normalize_stage_keeps_both_chunk_ids_too() -> None:
    """NORMALIZE присваивает общий tag_id и не должен затирать привязку к чанкам.

    Именно тут привязка терялась: концы связей переводятся по `tag_id`, но сама запись
    сущности при этом не накапливала `chunk_ids` второго чанка.
    """
    ctx = _ctx()
    first = _record(ctx, "Config Service", "chk:aaa")
    second = _record(ctx, "Config Service", "chk:bbb")
    ctx.entities = [first, second]

    NormalizeStage("").run(ctx)

    assert len({entity["tag_id"] for entity in ctx.entities}) == 1, (
        "одно имя должно получать один tag_id, иначе это две разные ноды"
    )
    assert set(first["chunk_ids"]) == {"chk:aaa"}
    assert set(second["chunk_ids"]) == {"chk:bbb"}


def test_same_pair_from_two_chunks_is_not_two_edges() -> None:
    """Одна пара из двух чанков - одна связь: размножать её нельзя.

    Отдельное утверждение, потому что потеря привязки и размножение связи - противоположные
    поломки. Считается и то, и другое: и число связей, и то, сколько чанков за ними стоит.
    """
    ctx = _ctx()
    ctx.entities = [
        _record(ctx, "Config Service", "chk:aaa"),
        _record(ctx, "Redis", "chk:bbb"),
    ]
    ctx.entity_edges = [
        {"from": "Config Service", "to": "Redis", "kind": "REQUIRES_CONSTRAINT", "confidence": 0.9},
    ]

    NormalizeStage("").run(ctx)

    assert len(ctx.entity_edges) == 1, "одна пара - одна связь"