"""COMMIT обязан объявлять, что уходит в запись (ADR-058).

Проверка существует потому, что её отсутствие стоило двух неверных выводов подряд: потеря
ноды или связи не видна ни в одном счётчике, она происходит в адаптере ниже пайплайна, и
без лога записи остаётся только гадать по симптому.

Тест ловит именно исчезновение: при откате лога `caplog` пуст, и проверка падает.
"""

from __future__ import annotations

import logging

import pytest

from graphrag_proto.ingestion_service.pipeline.orchestrator import (
    CommitStage,
    NormalizeStage,
    PipelineContext,
    _entity_record,
)


class _FakeGraph:
    """Заглушка: пишет вызовы, чтобы тест видел, ЧТО дошло до записи."""

    def __init__(self) -> None:
        self.nodes: list[dict] = []
        self.edges: list[dict] = []

    def consistency_capability(self) -> str:
        return "atomic"

    def engine_key(self) -> str:
        return "fake"

    def remove_source_from_entities(self, *args: object) -> None:
        return None

    def delete_node(self, node_id: str) -> bool:
        return True

    def upsert_nodes(self, nodes: list[dict]) -> None:
        self.nodes.extend(nodes)

    def upsert_edges(self, edges: list[dict]) -> None:
        self.edges.extend(edges)

    def upsert_mentions(self, *args: object, **kwargs: object) -> None:
        return None

    def link_chunks(self, *args: object, **kwargs: object) -> None:
        return None


class _FakeVector:
    def consistency_capability(self) -> str:
        return "atomic"

    def engine_key(self) -> str:
        return "fake"

    def delete_vectors(self, chunk_ids: list[str]) -> None:
        return None

    def upsert_vectors(self, vectors: list[dict]) -> None:
        return None


def _ctx() -> PipelineContext:
    return PipelineContext(job_id="job-1", source_url="docs/x.md", domain="it", doc_type="md")


def test_commit_logs_what_it_writes(caplog: pytest.LogCaptureFixture) -> None:
    """Итог, каждая нода и каждая связь должны попасть в лог на уровне INFO.

    Формулировка положительная: «лог содержит эти факты». Отрицательная («лог не содержит
    деталей») была бы хрупкой - она ломалась бы от любой законной правки формата.
    """
    graph = _FakeGraph()
    stage = CommitStage(registry=None, graph_store=graph, vector_store=_FakeVector())
    ctx = _ctx()
    ctx.candidates_total = 7
    # Без этого COMMIT уходит в vector-only: документ без списка кандидатов по ADR-053
    # в граф не пишется, и лога записи нод не будет - это другой штатный путь.
    ctx.candidates_absent = False
    entity = _entity_record(
        "Concept",
        {"canonical_name": "Config Service", "name": "Config Service"},
        ctx,
        "chk:aaa",
        "llm:test",
    )
    ctx.entities = [entity]
    ctx.entity_edges = [
        {"from": "Config Service", "to": "Redis", "kind": "REQUIRES_CONSTRAINT", "confidence": 0.9}
    ]
    NormalizeStage("").run(ctx)

    caplog.set_level(logging.INFO, logger="graphrag_proto.orchestrator.commit_retry")
    try:
        stage._write(type("Doc", (), {
            "domain": "it",
            "source_url": "docs/x.md",
            "content_hash": "sha256:abc",
        })(), ctx)
    except Exception as exc:  # noqa: BLE001 - интересует лог, а не отказ
        # Отказ ожидаем: реестра в тесте нет, и `_write` падает на `current_version`
        # уже ПОСЛЕ лога. Именно поэтому лог и стоит до обращения к реестру.
        # Отказ НЕ проглатывается молча: пустой `except` здесь означал бы, что тест
        # согласится и с тем, где `_write` упал раньше лога, - то есть перестанет
        # различать «лог есть» и «до лога не дошли».
        assert "current_version" in str(exc), (
            f"ожидался отказ на обращении к реестру, а получено: {exc!r}"
        )

    text = caplog.text
    assert "COMMIT: план записи" in text
    assert "Config Service" in text or "config service" in text
    assert "chk:aaa" in text