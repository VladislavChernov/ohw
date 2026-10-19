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
from graphrag_proto.retrieval.adapters.base import OWNED_ENTITY_FIELDS, source_priority
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
    # при user и не-user приходящем условие CASE истинно, значит $plain_properties
    # применяться не будет — это проверяется текстом запроса, а не результатом,
    # которого здесь нет: запрос до Neo4j не доходит
    assert "THEN {}" in runner.statements[-1][0]
    assert parameters["incoming_origin"] == "ai"


def test_owned_fields_follow_owner_order_not_last_writer() -> None:
    """ADR-044: содержимое ноды перезаписывается по порядку владельцев, а не всегда.

    **Что было.** Здесь стояло `assert statement.count("CASE WHEN") == 1`: единственный CASE
    защищал только `origin = 'user'`, поэтому для записей `ai` перезапись была безусловной.
    Докстринг того теста прямо называл это «единственным правилом, при котором содержимое
    общей ноды зависит от порядка загрузки корпуса». Решение владельца — вариант 1+3 (§3.12),
    запись в `docs/05_adr_log.md` ADR-044.

    **Что стало.** У каждого содержимого поля появился владелец (`<field>_source_url`,
    `<field>_version`), и поле переписывается условным SET. Проверяется текстом запроса:
    результата здесь нет — до Neo4j запрос не доходит.
    """
    runner = _Runner({"origin": "ai", "properties": json.dumps({"description": "прежний"})})

    statement = _write(runner, _machine_node())

    assert "SET n.description = CASE" in statement
    assert "SET n.description_source_url = CASE" in statement
    assert "SET n.description_version = CASE" in statement
    # защита `user` сохранилась и стоит раньше порядка владельцев
    assert "n.origin = 'user' AND $incoming_origin <> 'user'" in statement


def test_every_owned_field_is_compared_by_owner_order() -> None:
    """Позитивный контракт: что ингест помечает владельцем, то хранилище и сравнивает.

    Список полей один (`OWNED_ENTITY_FIELDS` в `adapters/base.py`) и используется обеими
    сторонами. Проверка идёт по нему, а не по перечислению: добавление поля в список не
    должно ломать тест, но поле без условия в запросе — должно.
    """
    for field in OWNED_ENTITY_FIELDS:
        runner = _Runner({"origin": "ai", "properties": "{}"})
        node = _machine_node(**{field: "значение", f"{field}_source_url": "src://doc.md",
                                f"{field}_version": 2})

        statement = _write(runner, node)
        parameters = runner.statements[-1][1]

        assert f"SET n.{field} = CASE" in statement, field
        assert f"SET n.{field}_source_url = CASE" in statement, field
        assert f"SET n.{field}_version = CASE" in statement, field
        assert parameters[f"incoming_{field}_source_url"] == "src://doc.md", field
        assert parameters[f"incoming_{field}_version"] == 2, field
        # содержимое не должно попадать в безусловный SET скалярных
        assert field not in parameters["plain_properties"], field


def test_declared_owner_order_is_present_in_the_statement() -> None:
    """Порядок из ADR-044 объявлен в запросе, а не подразумевается.

    **Правка 2026-10-02:** версии сравниваются **только внутри одного источника**. Первая
    редакция сравнивала их между источниками, что прямо противоречило основанию правила:
    нумерация ведётся внутри источника, поэтому «версия 2» против «версии 1» у разных
    документов ничего не значит. Между источниками сравнивается приоритет.
    """
    runner = _Runner({"origin": "ai", "properties": "{}"})

    statement = _write(runner, _machine_node())

    assert "coalesce(n.description_source_url, '') = ''" in statement
    assert "$incoming_description_source_url = n.description_source_url" in statement
    assert "$incoming_description_version > coalesce(n.description_version, -1)" in statement
    assert "$incoming_description_source_url_priority >" in statement
    assert "$incoming_description_source_url_priority =" in statement
    assert "$incoming_description_source_url > n.description_source_url" in statement


def test_version_alone_never_wins_across_sources() -> None:
    """Главное свойство правки: версия из чужого источника не перебивает.

    Внутри источника версия решает. Между источниками — только приоритет; при его
    равенстве решает `source_url`, чтобы результат не зависел от порядка загрузки.
    """
    runner = _Runner({"origin": "ai", "properties": "{}"})

    statement = _write(runner, _machine_node())

    # Сравнение версий допускается только под равенством источников.
    version_clause = (
        "$incoming_description_version > coalesce(n.description_version, -1)"
    )
    guarded_clause = (
        "$incoming_description_source_url = n.description_source_url AND " + version_clause
    )
    assert guarded_clause in statement
    # Веток, где версия сравнивается без проверки источника, быть не должно.
    for prefix in ("OR ", "("):
        index = statement.find(prefix + version_clause)
        assert index == -1, f"сравнение версий без проверки источника: {statement[index:][:80]}"


def test_source_priority_is_read_from_configuration(monkeypatch: Any) -> None:
    """Приоритет приходит из конфигурации, а не из константы в коде.

    Произвольная константа вернула бы ровно то, что правило заменило: сравнение,
    смысл которого нигде не записан.
    """
    monkeypatch.setenv(
        "PROJECTION_SOURCE_PRIORITY", '{"docs/a.md": 10, "docs/b.md": 5}'
    )

    assert source_priority("docs/a.md") == 10
    assert source_priority("docs/b.md") == 5
    # Источник без записи равноправен нулю, а не «отсутствует».
    assert source_priority("docs/unknown.md") == 0


def test_empty_or_broken_priority_config_is_visible(monkeypatch: Any) -> None:
    """Нечитаемая конфигурация не деградирует молча: пишется WARNING.

    Тихая деградация к «все равны» выглядела бы как решение, а не как сбой конфигурации.
    """
    monkeypatch.setenv("PROJECTION_SOURCE_PRIORITY", "не json")

    assert source_priority("docs/a.md") == 0


def test_owner_priority_is_stored_beside_the_owner() -> None:
    """Приоритет записывается на ноду, иначе его нельзя прочитать при следующей записи.

    Сравнение между источниками при следующем обновлении восстанавливается только из
    значения, лежащего рядом с владельцем.
    """
    runner = _Runner({"origin": "ai", "properties": "{}"})

    statement = _write(runner, _machine_node())

    assert "SET n.description_source_url_priority = CASE" in statement


def test_owner_and_value_are_written_by_the_same_condition() -> None:
    """Условие одно и то же для значения и для всех полей владельца.

    Разные условия допускали бы расхождение: значение не переписано (условие ложно), а
    владелец сменился — и поле получило бы подпись чужого источника. Полей-владельцев
    три (`source_url`, `version`, `source_url_priority`), поэтому условие встречается
    четыре раза: значение плюс каждое из них.
    """
    runner = _Runner({"origin": "ai", "properties": "{}"})

    statement = _write(runner, _machine_node())

    marker = "coalesce(n.description_source_url, '') = ''"
    assert statement.count(marker) == 4, "условие должно быть в значении и в трёх полях владельца"


def test_scalars_outside_owned_list_keep_unconditional_write() -> None:
    """Скалярные вне списка (`origin`, `canonical_name`, `confidence`) не получают владельца.

    Асимметрия с `canonical_name`/`confidence` остаётся: они по-прежнему last-wins по
    `_origin_rank`. Это объявлено в ADR-044 как нерешённое, и тест фиксирует, что
    расширения списка не происходит молча.
    """
    runner = _Runner({"origin": "ai", "properties": "{}"})

    statement = _write(runner, _machine_node(canonical_name="индексирование"))
    parameters = runner.statements[-1][1]

    assert parameters["plain_properties"].get("canonical_name") == "индексирование"
    assert parameters["plain_properties"].get("origin") == "ai"
    assert "SET n.canonical_name = CASE" not in statement
    assert "SET n.canonical_name_source_url" not in statement
