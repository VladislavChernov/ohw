"""Политика версий: режим развёртывания, уборка осиротевших связей и узлов.

Решение и обоснование — `CONCEPT.md` §5.1, `docs/02` §4.3–4.5, `docs/06` §2.4.
Правила удаления зафиксированы до кода (ADR-014 п. 16–19): канонический признак жизни —
непустой `chunk_ids`; порядок «связи, затем узлы»; подсчёт обязателен до удаления. Запись
факта требовалась в той же транзакции, что и удаление, **но в коде этого нет**: удаление
коммитится в Neo4j отдельными коммитами, факт пишется в SQLite третьим — см. ADR-014,
четвёртая корректирующая запись (пп. 20–23). Правило удаления от этого не меняется,
не реализована только доставка факта.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from graphrag_proto.ingestion_service.retention_policy import (
    DEFAULT_RETENTION_MODE,
    RETENTION_MODE_ENV,
    RetentionPolicy,
    run_orphan_cleanup,
)
from graphrag_proto.ingestion_service.storage.registry import JobStore
from graphrag_proto.retrieval.adapters.inmemory import InMemoryGraphStore

_LOG = logging.getLogger(__name__)


def _edge(from_id: str, chunk_ids: list[str] | None, source_ids: list[str]) -> dict[str, Any]:
    properties: dict[str, Any] = {"domain": "it", "source_ids": list(source_ids)}
    if chunk_ids is not None:
        properties["chunk_ids"] = list(chunk_ids)
    return {"from_id": from_id, "to_id": "t-1", "type": "REL", "properties": properties}


def test_default_mode_is_current_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Молчание означает безопасное поведение, а не «гигиену нужно включить руками»."""
    monkeypatch.delenv(RETENTION_MODE_ENV, raising=False)

    policy = RetentionPolicy()

    assert policy.mode == "current_only"
    assert policy.drops_orphans is True
    assert DEFAULT_RETENTION_MODE == "current_only"


@pytest.mark.parametrize("raw", ["current_only", "current-only", "CURRENT_ONLY", " current_only "])
def test_mode_accepts_aliases(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    """Значение приходит из env и из Config Service, форма может различаться."""
    monkeypatch.setenv(RETENTION_MODE_ENV, raw)

    assert RetentionPolicy().mode == "current_only"


def test_unknown_mode_falls_back_to_safe_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Опечатка в настройке не должна поднимать сервис.

    Худший случай при разборе неизвестного значения — лишняя уборка мусора, а не его
    накопление, поэтому дефолт именно безопасный.
    """
    monkeypatch.setenv(RETENTION_MODE_ENV, "curent_only")

    policy = RetentionPolicy()

    assert policy.mode == "current_only"
    assert policy.drops_orphans is True


def test_keep_history_disables_drops() -> None:
    """Вторая ветка объявлена, но не реализована: режим существует, уборка не идёт."""
    policy = RetentionPolicy("keep_history")

    assert policy.drops_orphans is False


def test_archive_warning_emitted_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Фоновая задача проходит регулярно; предупреждение на каждом проходе засоряет лог.

    Факт однократности — и есть смысл: предупреждение, которое видно всегда, не читают
    именно тогда, когда оно нужно.
    """
    policy = RetentionPolicy("keep_history")

    first = policy.check_archive()
    second = policy.check_archive()

    assert first is not None
    assert "архив" in first
    assert second is None


def test_no_archive_warning_for_safe_mode() -> None:
    """В безопасном режиме предупреждать не о чем."""
    assert RetentionPolicy("current_only").check_archive() is None


def test_cleanup_dry_runs_before_deleting(tmp_path: Path) -> None:
    """Подсчёт обязателен: цена ошибки предиката необратима и невидима.

    Здесь проверяется порядок — до удаления делается проход с `dry_run`, и именно он
    определяет, сколько связей подлежат удалению.
    """
    graph = InMemoryGraphStore()
    graph.upsert_edges([_edge("e-1", [], [])])
    registry = JobStore(tmp_path / "jobs.db")
    registry.create("job-1", source_url="docs://a.md", domain="it", doc_type="md")

    fact = run_orphan_cleanup(graph, registry, job_id="job-1", domain="it")

    assert fact["skipped"] is False
    assert fact["planned_relations"] == 1
    assert fact["removed_relations"] == 1
    assert not graph.verify_edge("e-1", "t-1", "REL")


def test_cleanup_records_fact_in_registry(tmp_path: Path) -> None:
    """Факт уборки — число в own-канале, а не текст сообщения.

    Иначе потерянная уборка читается как «удалять нечего»: правдоподобно и неверно.
    """
    graph = InMemoryGraphStore()
    graph.upsert_edges([_edge("e-1", [], [])])
    registry = JobStore(tmp_path / "jobs.db")
    registry.create("job-1", source_url="docs://a.md", domain="it", doc_type="md")

    run_orphan_cleanup(graph, registry, job_id="job-1", domain="it")

    fact = registry.orphan_cleanup("job-1")
    assert fact is not None
    assert fact["removed_relations"] == 1
    assert fact["mode"] == "current_only"


def test_cleanup_keeps_planned_and_removed_separate(tmp_path: Path) -> None:
    """Расхождение подсчёта и удаления — сигнал о гонке, и его нельзя терять.

    Подсчёт и удаление разделены по времени: между проходами может прийти другой ingest.
    Схлопывание их в одно поле превратило бы гонку в невидимую.
    """
    graph = InMemoryGraphStore()
    graph.upsert_edges([_edge("e-1", [], [])])
    registry = JobStore(tmp_path / "jobs.db")
    registry.create("job-1", source_url="docs://a.md", domain="it", doc_type="md")

    run_orphan_cleanup(graph, registry, job_id="job-1", domain="it")

    fact = registry.orphan_cleanup("job-1")
    assert fact is not None
    assert {"planned_relations", "removed_relations"} <= set(fact)


def test_cleanup_absent_fact_is_none_not_zero(tmp_path: Path) -> None:
    """Отсутствие наблюдения и ноль удалённого — разные вещи.

    Ноль в отчёте означал бы «уборка прошла и ничего не нашла», а `None` — «уборки не
    было»; слияние их даёт в среднем правдоподобное и неверное значение.
    """
    registry = JobStore(tmp_path / "jobs.db")

    assert registry.orphan_cleanup("job-none") is None


def test_cleanup_skips_in_keep_history_mode(tmp_path: Path) -> None:
    """Второй режим объявлен, но не реализован: уборка не выполняется и факт не пишется."""
    graph = InMemoryGraphStore()
    graph.upsert_edges([_edge("e-1", [], [])])
    registry = JobStore(tmp_path / "jobs.db")
    registry.create("job-1", source_url="docs://a.md", domain="it", doc_type="md")

    fact = run_orphan_cleanup(
        graph, registry, job_id="job-1", domain="it", policy=RetentionPolicy("keep_history")
    )

    assert fact["skipped"] is True
    assert graph.verify_edge("e-1", "t-1", "REL")
    assert registry.orphan_cleanup("job-1") is None


def test_orphan_cleanup_table_created_on_legacy_database(tmp_path: Path) -> None:
    """Новая таблица, а не колонка: у развёрнутой базы не должно быть миграции.

    `CREATE TABLE IF NOT EXISTS` создаёт её на месте; колонка потребовала бы `ALTER TABLE`
    везде, где пайплайн уже отработал.
    """
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE job_stages (job_id TEXT NOT NULL, stage TEXT NOT NULL, "
        "status TEXT NOT NULL, message TEXT, ts TEXT NOT NULL, PRIMARY KEY (job_id, stage))"
    )
    conn.commit()
    conn.close()

    registry = JobStore(db)
    registry.create("job-1", source_url="docs://a.md", domain="it", doc_type="md")
    registry.record_orphan_cleanup(
        "job-1", "it", planned_relations=2, removed_relations=2, planned_nodes=0, removed_nodes=0, mode="current_only"
    )

    assert registry.orphan_cleanup("job-1") is not None


def _analyzer(reg: Any, graph: Any, vector: Any) -> Any:
    from graphrag_proto.ingestion_service.pipeline.orchestrator import (
        Analyzer,
        ChunkStage,
        CommitStage,
        ContractStage,
        DedupStage,
        EmbedStage,
        ExtractStage,
        IngestStage,
        NormalizeStage,
        ValidateStage,
    )
    from graphrag_proto.ingestion_service.readers.registry import TxtReader

    return Analyzer(
        [
            IngestStage({"txt": TxtReader()}),
            ChunkStage(),
            EmbedStage(),
            ExtractStage(),
            NormalizeStage(""),
            DedupStage(),
            ContractStage(),
            ValidateStage(),
            CommitStage(reg, graph_store=graph, vector_store=vector, graph_optional=True),
        ]
    )


class _CapturingGraph(InMemoryGraphStore):
    """Запоминает, какие рёбра реально ушли в хранилище.

    Инвариант проверяется на границе, а не на промежуточном списке `entity_edges`.
    Именно на этой границе опора терялась: `chunk_ids` проставлялись связям в оркестраторе,
    но не копировались в свойства ребра, и в хранилище попадала связь без опоры - то есть
    неотличимая от структурной. Тест, смотрящий только на промежуточный список или
    проверяющий, что уборка пощадила связь, проходил: пощадить её было нечего, кандидатом
    она не была никогда.
    """

    def __init__(self) -> None:
        super().__init__()
        self.edge_batches: list[list[dict[str, Any]]] = []

    def upsert_edges(self, edges: list[dict[str, Any]]) -> None:
        self.edge_batches.append([dict(edge) for edge in edges])
        super().upsert_edges(edges)

    def edge_properties(self, from_id: str, to_id: str) -> dict[str, Any] | None:
        for batch in self.edge_batches:
            for edge in batch:
                if edge.get("from_id") == from_id and edge.get("to_id") == to_id:
                    return dict(edge.get("properties") or {})
        return None


def test_relation_reaches_the_store_with_chunk_support(tmp_path: Path) -> None:
    """Связь должна доходить до хранилища с непустым `chunk_ids`.

    Регрессия на дыру, найденную E2E-прогоном: `chunk_ids` проставлялись связям, но не
    попадали в свойства ребра. Предикат уборки опирается именно на это свойство, а связь
    без него неотличима от структурной (`chunk_ids IS NULL`) - то есть весь отбор по
    политике `current_only` оказывался пустым на любом корпусе, а уборка не могла удалить
    ничего. Юнит-тесты этого не видели: они строили рёбра руками, уже с `chunk_ids`.
    """
    from graphrag_proto.ingestion_service.pipeline.orchestrator import PipelineContext
    from graphrag_proto.ingestion_service.storage.registry import DocumentRegistry
    from graphrag_proto.retrieval.adapters.inmemory import InMemoryVectorStore

    reg = DocumentRegistry(tmp_path / "r.db")
    graph = _CapturingGraph()
    analyzer = _analyzer(reg, graph, InMemoryVectorStore())
    src = tmp_path / "d.txt"
    src.write_text("требование контракт сроки согласование договор", encoding="utf-8")
    tags = [
        {"tag_id": "tag:it:требование", "canonical_name": "требование"},
        {"tag_id": "tag:it:контракт", "canonical_name": "контракт"},
    ]

    analyzer.run(
        PipelineContext(
            job_id="j1",
            domain="it",
            doc_type="txt",
            source_url="src://d.txt",
            source_path=str(src),
            tags=tags,
            links=[{"from_id": "tag:it:требование", "to_id": "tag:it:контракт", "type": "RELATED"}],
        )
    )

    properties = graph.edge_properties("tag:it:требование", "tag:it:контракт")
    assert properties is not None, "ручная связь не дошла до хранилища"
    assert properties.get("chunk_ids"), (
        "у связи нет chunk_ids в свойствах ребра: предикат уборки её не увидит, "
        f"получено {sorted(properties)}"
    )


def test_user_scoped_link_outlives_the_document_that_declared_it(tmp_path: Path) -> None:
    """Связь с `scope: "user"` переживает ревизию документа, документная - нет.

    Решение владельца от 2026-09-29. Различие не вводится отдельным правилом уборки, а
    следует из уже существующего дискриминатора: связь получает опору на чанки документа,
    и тогда `chunk_ids IS NOT NULL` делает её кандидатом, а связь без опоры остаётся
    структурной и уборкой не трогается. Поэтому `scope` - это признак происхождения
    утверждения, а не новая логика удаления.

    Проверяются оба случая на одном графе и одной ревизии, иначе тест показал бы, что
    одна из половин не работает, не показав, что другая работает.
    """
    from graphrag_proto.ingestion_service.pipeline.orchestrator import PipelineContext
    from graphrag_proto.ingestion_service.storage.registry import DocumentRegistry
    from graphrag_proto.retrieval.adapters.inmemory import (
        InMemoryGraphStore,
        InMemoryVectorStore,
    )

    reg = DocumentRegistry(tmp_path / "r.db")
    graph = InMemoryGraphStore()
    analyzer = _analyzer(reg, graph, InMemoryVectorStore())
    src = tmp_path / "d.txt"
    src.write_text("требование контракт сроки согласование договор", encoding="utf-8")
    tags = [
        {"tag_id": "tag:it:требование", "canonical_name": "требование"},
        {"tag_id": "tag:it:контракт", "canonical_name": "контракт"},
    ]
    links = [
        # Документная: по умолчанию, объявлять нечего.
        {"from_id": "tag:it:требование", "to_id": "tag:it:контракт", "type": "DOC_SCOPED"},
        # Пользовательская: принадлежит человеку, а не документу.
        {
            "from_id": "tag:it:контракт",
            "to_id": "tag:it:требование",
            "type": "USER_SCOPED",
            "scope": "user",
        },
    ]

    analyzer.run(
        PipelineContext(
            job_id="j1",
            domain="it",
            doc_type="txt",
            source_url="src://d.txt",
            source_path=str(src),
            tags=tags,
            links=links,
        )
    )
    assert graph.verify_edge("tag:it:требование", "tag:it:контракт", "DOC_SCOPED")
    assert graph.verify_edge("tag:it:контракт", "tag:it:требование", "USER_SCOPED")

    # Ревизия того же документа без ручного ввода: документная опора исчезает.
    src.write_text("совершенно другой текст без прежних утверждений", encoding="utf-8")
    analyzer.run(
        PipelineContext(
            job_id="j2",
            domain="it",
            doc_type="txt",
            source_url="src://d.txt",
            source_path=str(src),
            tags=tags,
        )
    )
    graph.delete_orphans("it", dry_run=False)

    assert not graph.verify_edge("tag:it:требование", "tag:it:контракт", "DOC_SCOPED"), (
        "документная связь пережила ревизию: она должна уйти вместе с документом"
    )
    assert graph.verify_edge("tag:it:контракт", "tag:it:требование", "USER_SCOPED"), (
        "пользовательская связь удалена вместе с документом: scope не дал ей отличиться"
    )


def test_manual_link_is_supported_while_its_document_is_alive(tmp_path: Path) -> None:
    """Ручная связь держится на чанках документа и переживает уборку, пока документ жив.

    Регрессия на дыру из `docs/02`: `chunk_ids` проставлялись сущностям, но не связям, и
    предикат отбора опирается именно на `chunk_ids`. Связь без него неотличима от
    структурной, то есть ручное утверждение пользователя неубираемо навсегда: ни снятие
    тега, ни снятие самой связи ничего бы не удаляли.
    """
    from graphrag_proto.ingestion_service.pipeline.orchestrator import PipelineContext
    from graphrag_proto.ingestion_service.storage.registry import DocumentRegistry
    from graphrag_proto.retrieval.adapters.inmemory import (
        InMemoryGraphStore,
        InMemoryVectorStore,
    )

    reg = DocumentRegistry(tmp_path / "r.db")
    graph = InMemoryGraphStore()
    vector = InMemoryVectorStore()
    analyzer = _analyzer(reg, graph, vector)
    src = tmp_path / "d.txt"
    src.write_text("требование контракт сроки согласование договор", encoding="utf-8")
    tags = [
        {"tag_id": "tag:it:требование", "canonical_name": "требование"},
        {"tag_id": "tag:it:контракт", "canonical_name": "контракт"},
    ]
    link = {"from_id": "tag:it:требование", "to_id": "tag:it:контракт", "type": "RELATED"}

    ctx = PipelineContext(
        job_id="j1",
        domain="it",
        doc_type="txt",
        source_url="src://d.txt",
        source_path=str(src),
        tags=tags,
        links=[link],
    )
    analyzer.run(ctx)

    assert graph.verify_edge("tag:it:требование", "tag:it:контракт", "RELATED")
    assert graph.delete_orphans("it", dry_run=False) == 0, "опора связи потеряна, уборка съела живое"
    assert graph.verify_edge("tag:it:требование", "tag:it:контракт", "RELATED")


def test_manual_link_dies_with_the_version_that_asserted_it(tmp_path: Path) -> None:
    """Снятие документа убирает и его ручную связь: опора была только от него.

    Вторая половина той же регрессии: проставить опору мало, надо чтобы при перезагрузке
    версии она снималась. Предикат здесь — `chunk_ids` (ADR-014 п. 16), а снятие источника
    чистит `chunk_ids` и у нод, и у рёбер (второй запрос `_remove_source`).
    """
    from graphrag_proto.ingestion_service.pipeline.orchestrator import PipelineContext
    from graphrag_proto.ingestion_service.storage.registry import DocumentRegistry
    from graphrag_proto.retrieval.adapters.inmemory import (
        InMemoryGraphStore,
        InMemoryVectorStore,
    )

    reg = DocumentRegistry(tmp_path / "r.db")
    graph = InMemoryGraphStore()
    vector = InMemoryVectorStore()
    analyzer = _analyzer(reg, graph, vector)
    src = tmp_path / "d.txt"
    src.write_text("требование контракт сроки согласование договор", encoding="utf-8")
    tags = [
        {"tag_id": "tag:it:требование", "canonical_name": "требование"},
        {"tag_id": "tag:it:контракт", "canonical_name": "контракт"},
    ]
    link = {"from_id": "tag:it:требование", "to_id": "tag:it:контракт", "type": "RELATED"}

    analyzer.run(
        PipelineContext(
            job_id="j1",
            domain="it",
            doc_type="txt",
            source_url="src://d.txt",
            source_path=str(src),
            tags=tags,
            links=[link],
        )
    )
    assert graph.verify_edge("tag:it:требование", "tag:it:контракт", "RELATED")

    src.write_text("совершенно другой текст без прежних утверждений", encoding="utf-8")
    analyzer.run(
        PipelineContext(
            job_id="j2",
            domain="it",
            doc_type="txt",
            source_url="src://d.txt",
            source_path=str(src),
            tags=tags,
        )
    )

    removed = graph.delete_orphans("it", dry_run=False)
    assert removed >= 1, "связь, которую сняли вместе с версией документа, осталась в графе"
    assert not graph.verify_edge("tag:it:требование", "tag:it:контракт", "RELATED")
