"""Правила слияния нод: зафиксированы тестом или только прочитаны в коде.

`docs/02` §4.1 называет три группы полей с разным поведением и честно отмечает, какая
из них тестами не закреплена. Эти тесты закрывают те три места. Причина в их появлении
та же, что у регрессии на мёртвый `graph_boost`: правило, написанное в документе, но
не закреплённое кодом, разъезжается с кодом молча.

Термины:
* **документ** — одна джоба, свой `PipelineContext`; `DedupStage` видит только его;
* **нода** — узел графа с одним `node_id`, в который пишут разные джобы.
"""

from __future__ import annotations

import json
from typing import Any

from graphrag_proto.ingestion_service.pipeline.orchestrator import (
    DedupStage,
    PipelineContext,
)
from graphrag_proto.retrieval.adapters.neo4j import _upsert_nodes


def _context(*entities: dict[str, Any]) -> PipelineContext:
    return PipelineContext(
        job_id="j",
        domain="it",
        doc_type="md",
        source_url="src://doc.md",
        chunks=["текст документа"],
        entities=[dict(e) for e in entities],
    )


def _entity(key: str, origin: str = "ai", **extra: Any) -> dict[str, Any]:
    record = {
        "tag_id": f"tag:it:{key}",
        "canonical": key,
        "canonical_name": key,
        "name": key,
        "origin": origin,
        "chunk_ids": ["chk:1"],
        "sources": ["src://doc.md"],
    }
    record.update(extra)
    return record


# --- внутри одного документа: две противоположные политики --------------------


def test_description_of_one_node_inside_a_document_is_first_wins() -> None:
    """Внутри документа `description` заполняется один раз и больше не меняется.

    Правило `if property_name not in current` — «первый писатель выигрывает». Проверяется
    явно, потому что это единственное место, где описание документа может быть потеряно
    навсегда: повторная загрузка того же хэша — no-op, и изменить содержимое, не изменив
    файл, нельзя.
    """
    ctx = _context(
        _entity("индексирование", description="ускоряет поиск"),
        _entity("индексирование", description="нужно для полнотекстового поиска"),
    )

    DedupStage().run(ctx)

    assert len(ctx.entities) == 1
    assert ctx.entities[0]["description"] == "ускоряет поиск"


def test_name_of_one_node_inside_a_document_is_last_wins() -> None:
    """А `name` внутри документа — наоборот, последний: правило `_origin_rank >= current_rank`.

    Два противоположных правила на одной ноде одного документа: имя приходит из последнего
    извлечения, описание — из первого. Это выглядит как недосмотр, поэтому и закрепляется
    тестом: пока это осознанное поведение, его можно обсуждать; однажды не закрепи — и
    следующий прочитавший не отличит решение от ошибки.
    """
    ctx = _context(
        _entity("индексация", name="индексация", canonical_name="индексация"),
        _entity("индексация", name="индексирование", canonical_name="индексирование"),
    )

    DedupStage().run(ctx)

    assert len(ctx.entities) == 1
    assert ctx.entities[0]["canonical_name"] == "индексирование"


def test_higher_origin_rank_wins_inside_a_document() -> None:
    """Ранг важнее порядка: `user` перебивает `ai` независимо от того, пришёл раньше или позже."""
    ctx = _context(
        _entity("термин", origin="ai", name="из текста"),
        _entity("термин", origin="user", name="ручное"),
    )

    DedupStage().run(ctx)

    assert ctx.entities[0]["name"] == "ручное"

    ctx_reversed = _context(
        _entity("термин", origin="user", name="ручное"),
        _entity("термин", origin="ai", name="из текста"),
    )

    DedupStage().run(ctx_reversed)

    assert ctx_reversed.entities[0]["name"] == "ручное"


# --- между документами: при записи --------------------------------------------


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def data(self) -> list[dict[str, Any]]:
        return self._rows

    def consume(self) -> _Result:
        return self


class _Runner:
    """Пишет запросы в Neo4j и подставляет «уже существующую» ноду."""

    def __init__(self, existing: dict[str, Any] | None = None) -> None:
        self.existing = existing
        self.statements: list[tuple[str, dict[str, Any]]] = []

    def run(self, query: str, parameters: dict[str, Any] | None = None) -> _Result:
        self.statements.append((query, dict(parameters or {})))
        if "RETURN n.origin AS origin" in query:
            return _Result([self.existing] if self.existing else [])
        return _Result([])

    def consume(self) -> _Result:
        return self


def _write(runner: _Runner, node: dict[str, Any]) -> str:
    _upsert_nodes(runner, [node])  # type: ignore[arg-type]
    return runner.statements[-1][0]


def _machine_node(origin: str = "ai", **extra: Any) -> dict[str, Any]:
    return {
        "node_id": "tag:it:индексирование",
        "labels": ["ContextNode"],
        "properties": {"origin": origin, "description": "из другого документа", **extra},
    }


def test_system_node_is_not_protected_at_write_time() -> None:
    """`system`-нода перезаписывается машинной записью — в отличие от `user`.

    Расхождение зафиксировано, потому что оно достижимо и оба его конца по отдельности
    выглядят правильными: внутри документа `system` (ранг 2) переживает `ai` (ранг 1), а при
    записи защита есть только у `user`. Итог — один и тот же узел трактуется двумя разными
    правилами в зависимости от того, какая граница пересечена, и результат зависит от того,
    откуда пришла запись. Тест фиксирует **фактическое** поведение; менять его нужно
    вместе с решением из §4.2, а не этим тестом.
    """
    runner = _Runner({"origin": "system", "properties": json.dumps({"description": "заглушка"})})

    statement = _write(runner, _machine_node())

    # `user` защищён условием в запросе, `system` — нет
    assert "n.origin = 'user'" in statement
    parameters = runner.statements[-1][1]
    assert parameters["incoming_origin"] == "ai"
    # CASE срабатывает только на user, поэтому скалярные свойства применяются
    assert "n.origin = 'user' AND $incoming_origin <> 'user'" in statement


def test_user_node_keeps_scalar_properties_from_machine_write() -> None:
    """Контроль к предыдущему тесту: `user` действительно не перезаписывается."""
    runner = _Runner({"origin": "user", "properties": json.dumps({"canonical": "ручное"})})

    _write(runner, _machine_node())

    parameters = runner.statements[-1][1]
    # при user и не-user приходящем ус��овие CASE истинно, значит $scalar_properties
    # применяться не будет — это проверяется текстом запроса, а не результатом,
    # которого здесь нет: запрос до Neo4j не доходит
    assert "THEN {}" in runner.statements[-1][0]
    assert parameters["incoming_origin"] == "ai"


def test_last_writer_wins_for_equal_origin() -> None:
    """При равном `origin = ai` побеждает пришедший последним: условия в запросе нет.

    Это единственное правило, при котором содержимое общей ноды зависит от порядка загрузки
    корпуса, и оно не закреплено ни одним тестом. Фиксируем текстом запроса: условного SET
    для не-user записей нет, значит перезапись безусловна.
    """
    runner = _Runner({"origin": "ai", "properties": json.dumps({"description": "прежний"})})

    statement = _write(runner, _machine_node())

    assert statement.count("CASE WHEN") == 1
    # единственный CASE защищает только user; для ai применяется $scalar_properties
    assert "n.origin = 'user'" in statement
    assert "THEN {}" in statement
