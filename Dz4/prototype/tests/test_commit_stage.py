"""COMMIT (L2-04/L2-05, A-2): запись в провайдеры, атомарность, идемпотентность, soft-delete."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from graphrag_proto.ingestion_service.pipeline.chunker import Chunker
from graphrag_proto.ingestion_service.pipeline.orchestrator import (
    MANUAL_NODE_VERSION,
    Analyzer,
    ChunkStage,
    CommitStage,
    CommitStageError,
    ContractStage,
    DedupStage,
    EmbedStage,
    ExtractStage,
    IngestStage,
    NormalizeStage,
    PipelineContext,
    ValidateStage,
    _chunk_id,
    _with_commit_retry,
    soft_delete_source,
)
from graphrag_proto.ingestion_service.projection import (
    InMemoryProjectionStateStore,
    source_projection_revision,
)
from graphrag_proto.ingestion_service.readers.registry import TxtReader
from graphrag_proto.ingestion_service.storage.registry import DocumentRegistry
from graphrag_proto.retrieval.adapters.base import Embedder
from graphrag_proto.retrieval.adapters.deterministic import deterministic_embedding
from graphrag_proto.retrieval.adapters.inmemory import InMemoryGraphStore, InMemoryVectorStore
from graphrag_proto.retrieval.adapters.llm import FakeLLM

CONTENT_1 = "кэширование данные дедупликация алгоритм базы данных\n"
CONTENT_2 = "индекс поиск дедупликация граф знаний через рёбра и вершины\n"
DEDUP_TAG_ID = "tag:it:дедупликация"

# ADR-049: детерминированной заглушки больше нет, поэтому ноды в графе этого файла
# появляются только потому, что тест сам объявляет их ответом модели. Раньше они
# появлялись потому, что LLM не было, — то есть четыре теста ниже держались на молчании
# и на подстановке слов текста вместо сущностей.
#
# Форма ответа — та, которую отдаёт модель (ADR-039, ADR-037): `canonical_name` обязателен,
# `tag_id` запрещён и платформой не принимается, идентичность считается из имени. Нода
# получает `tag_id = tag:it:дедупликация` сама, из `_identity_key(canonical_name)`.
_ENTITY_PROFILE: dict[str, Any] = {
    "profile": {"name": "it"},
    "extraction": {
        "llm_enabled": True,
        "prompt_template": {
            "id": "extract_commit_it_v1",
            "system": "Extract context nodes and links.",
            "user": "Return JSON with tags and links.",
        },
    },
    # Те же параметры, что давал `build_chunker()` по умолчанию, — чтобы появление профиля
    # в этой сборке не сдвинуло разбиение на чанки и не изменило ожидания тестов про
    # векторную ось. Профиль без `chunking` тоже откатился бы на дефолты, но объявлять их
    # здесь лучше явно: иначе смена дефолта тихо меняет смысл этих тестов.
    "chunking": {"strategy": "sliding_window", "chunk_size": 512, "overlap": 64},
}

_LLM_ANSWER: dict[str, Any] = {
    "tags": [
        {
            "canonical_name": "дедупликация",
            "name": "дедупликация",
            "aliases": ["дедупликационный проход"],
            "origin": "ai",
            "confidence": 0.8,
        }
    ],
    "relationships": [],
}


@pytest.fixture(autouse=True)
def _llm_extraction_declared(monkeypatch: Any) -> None:
    """Экстракция LLM в этом файле объявлена намеренно.

    Отдельная фикстура, а не `monkeypatch` в каждом тесте: `build_analyzer` ниже собирает
    конвейер, и «объявлен ли LLM» — свойство файла, а не отдельного теста. Раньше здесь
    ничего не объявлялось, и ветка `not _llm_extraction_enabled()` была дорогой по
    умолчанию.
    """
    monkeypatch.setenv("EXTRACT_LLM", "true")


def build_analyzer(
    tmp_path: Path,
    graph: InMemoryGraphStore | None,
    vector: InMemoryVectorStore,
    embedder: Embedder | None = None,
    chunker: Chunker | None = None,
    projection_state: InMemoryProjectionStateStore | None = None,
) -> tuple[Analyzer, DocumentRegistry]:
    registry = DocumentRegistry(tmp_path / "commit.db")
    stages = [
        IngestStage({"txt": TxtReader()}),
        ChunkStage(chunker),
        EmbedStage(embedder),
        ExtractStage(
            llm=FakeLLM(text=json.dumps(_LLM_ANSWER, ensure_ascii=False), is_fake=False),
            profile_fetcher=lambda _domain: deepcopy(_ENTITY_PROFILE),
            optional_failure=True,
        ),
        NormalizeStage(""),
        DedupStage(),
        ContractStage(),
        ValidateStage(),
        CommitStage(
            registry,
            graph_store=graph,
            vector_store=vector,
            graph_optional=True,
            projection_state_store=projection_state,
        ),
    ]
    return Analyzer(stages, profile_fetcher=lambda _domain: deepcopy(_ENTITY_PROFILE)), registry


def run_source(
    analyzer: Analyzer,
    src: Path,
    *,
    tags: list[dict[str, Any]] | None = None,
    links: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> PipelineContext:
    ctx = PipelineContext(
        job_id="j",
        domain="it",
        doc_type="txt",
        source_url="src://d.txt",
        source_path=str(src),
        tags=tags or [],
        links=links or [],
        metadata=metadata or {},
    )
    analyzer.run(ctx)
    return ctx


def test_document_registry_source_lock_serializes_same_identity(tmp_path: Path) -> None:
    registry = DocumentRegistry(tmp_path / "source-lock.db")
    entered = threading.Event()
    release = threading.Event()
    second_entered = threading.Event()

    def hold_source_lock() -> None:
        with registry.source_lock("it", "src://same.txt"):
            entered.set()
            release.wait(timeout=2)

    def acquire_source_lock() -> None:
        with registry.source_lock("it", "src://same.txt"):
            second_entered.set()

    first = threading.Thread(target=hold_source_lock)
    first.start()
    assert entered.wait(timeout=1)
    second = threading.Thread(target=acquire_source_lock)
    second.start()
    assert not second_entered.wait(timeout=0.05)
    release.set()
    first.join(timeout=1)
    second.join(timeout=1)
    assert second_entered.is_set()


def test_commit_writes_context_nodes_edges_vectors(tmp_path: Path) -> None:
    graph, vector = InMemoryGraphStore(), InMemoryVectorStore()
    analyzer, registry = build_analyzer(tmp_path, graph, vector)
    src = tmp_path / "d.txt"
    src.write_text(CONTENT_1, encoding="utf-8")
    ctx = run_source(analyzer, src)

    assert ctx.commit_applied is True

    # ADR-046 п. 9: ноды-якоря источника в схеме нет. Носитель ревизии — сам Chunk,
    # а владелец выражен полем `source_url` (L2-03).
    chunk_ids = graph.list_chunk_ids_of_source("src://d.txt", "it")
    assert chunk_ids
    chunks = {chunk_id: graph.get_node(chunk_id) for chunk_id in chunk_ids}
    assert all(chunks[chunk_id]["_labels"] == ["Chunk"] for chunk_id in chunks)
    assert all(chunks[chunk_id]["source_url"] == "src://d.txt" for chunk_id in chunks)
    assert all(chunks[chunk_id]["domain"] == "it" for chunk_id in chunks)
    revisions = {chunks[chunk_id].get("projection_revision") for chunk_id in chunks}
    assert len(revisions) == 1 and None not in revisions, "все чанки документа делят одну ревизию"
    hashes = {chunks[chunk_id].get("content_hash") for chunk_id in chunks}
    assert len(hashes) == 1 and None not in hashes


    context_node = graph.get_node(DEDUP_TAG_ID)
    assert context_node is not None
    assert context_node["_labels"] == ["ContextNode"]
    assert context_node["tag_id"] == DEDUP_TAG_ID
    # ADR-049: метка ноды называет того, кто её создал. Раньше здесь стояло
    # `== "deterministic:v1"`, то есть тест закреплял метку заглушки, а тесты этого
    # файла держались на её молчаливом срабатывании.
    assert context_node["extractor_version"].startswith("llm:")
    assert context_node["extractor_version"] != MANUAL_NODE_VERSION
    assert context_node["source_ids"] == ["src://d.txt"]
    mentioned_chunks = {
        from_id
        for from_id, to_id, edge_type in graph._edges
        if to_id == DEDUP_TAG_ID and edge_type == "MENTIONS"
    }
    assert set(context_node["chunk_ids"]) == mentioned_chunks
    assert mentioned_chunks.issubset(chunk_ids)

    chunk_text = graph.get_node(chunk_ids[0])["text"]
    hits = vector.vector_search(deterministic_embedding(chunk_text), top_k=10)
    assert hits, "эмбеддинги чанков должны участвовать в поиске"
    assert any(DEDUP_TAG_ID in hit["context_ids"] for hit in hits)
    assert any(DEDUP_TAG_ID in hit["tag_ids"] for hit in hits)
    assert all(hit["revision"] == registry.data_revision("it") for hit in hits)


def test_chunk_revision_equals_revision_derived_from_its_owner(tmp_path: Path) -> None:
    """Гард ADR-046 п. 7: у каждого `Chunk` задан владелец, а его `projection_revision`
    равна ревизии, **вычисленной** из `content_hash` этого владельца и конфига.

    Формулировка положительная намеренно: «ревизия чанка равна вычисленной из его
    владельца» переживёт и удаление якоря, и появление новых полей, тогда как
    «ноды `Source` не существует» ломалась бы от первого законного изменения схемы.
    """
    graph, vector = InMemoryGraphStore(), InMemoryVectorStore()
    analyzer, _ = build_analyzer(tmp_path, graph, vector)
    src = tmp_path / "d.txt"
    src.write_text(CONTENT_1, encoding="utf-8")
    run_source(analyzer, src)

    config = os.environ.get("PROJECTION_CONFIG_FINGERPRINT", "default")
    chunk_ids = graph.list_chunk_ids_of_source("src://d.txt", "it")
    assert chunk_ids, "документ обязан оставить чанки в графе"
    for chunk_id in chunk_ids:
        chunk = graph.get_node(chunk_id)
        assert chunk is not None, chunk_id
        owner = str(chunk["source_url"])
        assert owner, "L2-03: чанк без владельца недопустим"
        content_hash = str(chunk["content_hash"])
        assert content_hash, "носитель ревизии обязан хранить отпечаток содержимого"
        assert chunk["projection_revision"] == source_projection_revision(content_hash, config)


def test_chunk_revision_changes_with_config_not_with_version(tmp_path: Path) -> None:
    """Ревизия выводится из содержимого и конфига, а не из номера версии документа:
    перезагрузка того же содержимого обязана оставить ревизию прежней."""
    graph, vector = InMemoryGraphStore(), InMemoryVectorStore()
    analyzer, _ = build_analyzer(tmp_path, graph, vector)
    src = tmp_path / "d.txt"
    src.write_text(CONTENT_1, encoding="utf-8")
    run_source(analyzer, src)
    first = graph.list_chunk_ids_of_source("src://d.txt", "it")
    before = graph.get_node(first[0])["projection_revision"]

    src.write_text(CONTENT_2, encoding="utf-8")
    run_source(analyzer, src)
    second = graph.list_chunk_ids_of_source("src://d.txt", "it")
    after = graph.get_node(second[0])["projection_revision"]
    assert before != after, "новое содержимое обязано менять ревизию"

    monkey_config = os.environ.get("PROJECTION_CONFIG_FINGERPRINT", "default")
    assert source_projection_revision(
        str(graph.get_node(second[0])["content_hash"]), monkey_config + "-changed"
    ) != after, "смена конфига обязана менять ревизию"


def test_commit_idempotent_noop(tmp_path: Path) -> None:
    graph, vector = InMemoryGraphStore(), InMemoryVectorStore()
    analyzer, registry = build_analyzer(tmp_path, graph, vector)
    src = tmp_path / "d.txt"
    src.write_text(CONTENT_1, encoding="utf-8")

    run_source(analyzer, src)
    before_ids = graph.list_chunk_ids_of_source("src://d.txt", "it")
    vectors_before = list(vector._vectors.keys())

    run_source(analyzer, src)
    latest = registry.latest_active("it", "src://d.txt")
    assert latest is not None and latest["version"] == 1
    assert graph.list_chunk_ids_of_source("src://d.txt", "it") == before_ids
    assert set(vector._vectors.keys()) == set(vectors_before)


def test_commit_reindex_replaces_stale_chunks(tmp_path: Path) -> None:
    graph, vector = InMemoryGraphStore(), InMemoryVectorStore()
    analyzer, registry = build_analyzer(tmp_path, graph, vector)
    src = tmp_path / "d.txt"

    src.write_text(CONTENT_1, encoding="utf-8")
    run_source(analyzer, src)
    chunk_ids = graph.list_chunk_ids_of_source("src://d.txt", "it")

    src.write_text(CONTENT_2, encoding="utf-8")
    run_source(analyzer, src)

    latest = registry.latest_active("it", "src://d.txt")
    assert latest is not None and latest["version"] == 2
    # идентификаторы чанков стабильны (source_url+index), контент перезаписан
    assert graph.list_chunk_ids_of_source("src://d.txt", "it") == chunk_ids
    assert len(vector._vectors) == len(chunk_ids)
    for chunk_id in chunk_ids:
        assert "граф знаний" in graph.get_node(chunk_id)["text"]


def test_vector_only_reindex_deletes_stale_chunks(tmp_path: Path) -> None:
    vector = InMemoryVectorStore()
    analyzer, registry = build_analyzer(tmp_path, None, vector)
    src = tmp_path / "d.txt"
    src.write_text("слово " * 2000, encoding="utf-8")
    run_source(analyzer, src)
    assert len(vector._vectors) > 1

    src.write_text("short", encoding="utf-8")
    run_source(analyzer, src)

    assert len(vector._vectors) == 1
    assert registry.latest_active("it", "src://d.txt")["version"] == 2


def test_same_content_with_tags_runs_graph_mutation(tmp_path: Path) -> None:
    graph, vector = InMemoryGraphStore(), InMemoryVectorStore()
    analyzer, registry = build_analyzer(tmp_path, graph, vector)
    src = tmp_path / "d.txt"
    src.write_text(CONTENT_1, encoding="utf-8")
    run_source(analyzer, src)

    run_source(
        analyzer,
        src,
        tags=[
            {
                "tag_id": "tag:it:manual",
                "canonical_name": "Manual tag",
                "origin": "user",
            }
        ],
    )

    assert graph.get_node("tag:it:manual") is not None
    assert registry.latest_active("it", "src://d.txt")["version"] == 1


def test_commit_transaction_rollback_on_error(tmp_path: Path) -> None:
    src = tmp_path / "d.txt"
    src.write_text(CONTENT_1, encoding="utf-8")

    class ExplodingVector(InMemoryVectorStore):
        def upsert_vectors(self, items) -> None:
            raise RuntimeError("сбой записи векторов")

    graph2 = InMemoryGraphStore()
    analyzer2, _ = build_analyzer(tmp_path, graph2, ExplodingVector())
    try:
        analyzer2.run(
            PipelineContext(job_id="j2", domain="it", doc_type="txt", source_url="src://d.txt", source_path=str(src))
        )
        assert False, "сбой эмбеддингов должен привести к ошибке пайплайна"
    except RuntimeError:
        pass
    # атомарность: узлы графа не записались, несмотря на то что вектор-ось упала
    assert graph2.list_chunk_ids_of_source("src://d.txt", "it") == []


def test_soft_delete_source_removes_chunks_keeps_context_nodes(tmp_path: Path) -> None:
    graph, vector = InMemoryGraphStore(), InMemoryVectorStore()
    analyzer, registry = build_analyzer(tmp_path, graph, vector)
    src = tmp_path / "d.txt"
    src.write_text(CONTENT_1, encoding="utf-8")
    run_source(analyzer, src)

    chunk_ids = graph.list_chunk_ids_of_source("src://d.txt", "it")
    assert chunk_ids

    assert soft_delete_source(registry, graph, vector, "it", "src://d.txt") is True
    assert registry.latest_active("it", "src://d.txt") is None
    assert graph.list_chunk_ids_of_source("src://d.txt", "it") == []
    for chunk_id in chunk_ids:
        assert graph.get_node(chunk_id) is None
        assert chunk_id not in vector._vectors
    # Context nodes сохраняются (историчность, ADR-014); ноды-якоря источника в схеме
    # нет, поэтому сохраняться тут нечему (ADR-046 п. 9).
    context_node = graph.get_node(DEDUP_TAG_ID)
    assert context_node is not None
    assert context_node["_labels"] == ["ContextNode"]
    assert context_node["tag_id"] == DEDUP_TAG_ID
    assert context_node["source_ids"] == []
    assert context_node["chunk_ids"] == []
    assert not any(edge_type == "MENTIONS" for _, _, edge_type in graph._edges)

    assert soft_delete_source(registry, graph, vector, "it", "src://d.txt") is False


def test_registry_rollback_soft_delete_roundtrip(tmp_path: Path) -> None:
    """2.5.5 (UC12-06): rollback_soft_delete возвращает deleted-версию в active."""
    graph, vector = InMemoryGraphStore(), InMemoryVectorStore()
    analyzer, registry = build_analyzer(tmp_path, graph, vector)
    src = tmp_path / "d.txt"
    src.write_text(CONTENT_1, encoding="utf-8")
    run_source(analyzer, src)

    assert registry.soft_delete("it", "src://d.txt") is True
    assert registry.latest_active("it", "src://d.txt") is None

    assert registry.rollback_soft_delete("it", "src://d.txt") is True
    latest = registry.latest_active("it", "src://d.txt")
    assert latest is not None and latest["status"] == "active" and latest["version"] == 1

    # второй rollback — no-op: deleted-записей не осталось
    assert registry.rollback_soft_delete("it", "src://d.txt") is False


def test_soft_delete_source_rolls_back_registry_when_stores_deny(tmp_path: Path) -> None:
    """2.5.5 (UC12-06): окончательный отказ удаления в хранилищах → реестр снова
    active, данные остались; повторная джоба проходит полный путь (L2-06)."""
    graph, vector = InMemoryGraphStore(), InMemoryVectorStore()
    analyzer, registry = build_analyzer(tmp_path, graph, vector)
    src = tmp_path / "d.txt"
    src.write_text(CONTENT_1, encoding="utf-8")
    run_source(analyzer, src)
    chunk_ids = graph.list_chunk_ids_of_source("src://d.txt", "it")
    assert chunk_ids

    class DenyingGraph(InMemoryGraphStore):
        """Чанки берёт из реального графа, но отказывает в удалении."""

        def __init__(self, real: InMemoryGraphStore) -> None:
            super().__init__()
            self._real = real

        def list_chunk_ids_of_source(self, source_url: str, domain: str) -> list[str]:
            return self._real.list_chunk_ids_of_source(source_url, domain)

        def delete_node(self, node_id: str) -> bool:
            raise RuntimeError("хранилище отказывает в удалении")

    with pytest.raises(RuntimeError, match="удалении"):
        soft_delete_source(registry, DenyingGraph(graph), vector, "it", "src://d.txt")

    # реестр компенсирован: документ снова active (не рассинхронизирован с осями)
    latest = registry.latest_active("it", "src://d.txt")
    assert latest is not None and latest["status"] == "active"
    # данные на месте — удаление не применилось ни к одной оси
    assert graph.list_chunk_ids_of_source("src://d.txt", "it") == chunk_ids
    assert len(vector._vectors) == len(chunk_ids)

    # повторная джоба проходит полный путь: реестр -> хранилища -> True
    assert soft_delete_source(registry, graph, vector, "it", "src://d.txt") is True
    assert registry.latest_active("it", "src://d.txt") is None
    assert graph.list_chunk_ids_of_source("src://d.txt", "it") == []
    assert not vector._vectors


def test_soft_delete_source_repeat_after_partial_delete_full_path(tmp_path: Path) -> None:
    """2.5.5 (UC12-06): сбой на середине удаления (частичное удаление) — реестр
    откатывается в active, повторная попытка безопасна (идемпотентность L2-06)."""
    graph, vector = InMemoryGraphStore(), InMemoryVectorStore()
    analyzer, registry = build_analyzer(tmp_path, graph, vector)
    src = tmp_path / "d.txt"
    src.write_text(CONTENT_1, encoding="utf-8")
    run_source(analyzer, src)
    chunk_ids = graph.list_chunk_ids_of_source("src://d.txt", "it")
    assert chunk_ids

    class FailAfterPartialDeleteVector(InMemoryVectorStore):
        """Удаляет первый чанк из реальной оси, затем падает (non-transient)."""

        def __init__(self, real: InMemoryVectorStore) -> None:
            super().__init__()
            self._real = real
            self.calls = 0

        def delete_vectors(self, items: list[str]) -> None:
            self.calls += 1
            if items:
                # частичное применение «вне журнала транзакции» + сбой движка
                self._real._vectors.pop(items[0], None)
            raise RuntimeError("сбой на середине удаления")

        # ADR-046 п. 9: перечисление чанков документа идёт только по оси вектора,
        # поэтому двойник обязан читать реальное состояние, а не своё пустое.
        def list_chunk_ids_of_source(self, source_url: str, domain: str) -> list[str]:
            return self._real.list_chunk_ids_of_source(source_url, domain)

    fail_vector = FailAfterPartialDeleteVector(vector)
    with pytest.raises(RuntimeError, match="середине"):
        soft_delete_source(registry, graph, fail_vector, "it", "src://d.txt")

    # реестр компенсирован в active; граф не тронут; из вектора убран один чанк
    latest = registry.latest_active("it", "src://d.txt")
    assert latest is not None and latest["status"] == "active"
    assert len(vector._vectors) == len(chunk_ids) - 1, "один вектор-чанк удалён до сбоя"

    # повторный вызов проходит полный путь: остаток удаляется идемпотентно (L2-06)
    assert soft_delete_source(registry, graph, vector, "it", "src://d.txt") is True
    assert registry.latest_active("it", "src://d.txt") is None
    assert graph.list_chunk_ids_of_source("src://d.txt", "it") == []
    assert not vector._vectors


def test_every_committed_source_gets_its_revision_row(tmp_path: Path) -> None:
    """Гард, найденный на стенде 2026-10-02: журнал ревизий оставался с одной строкой.

    ADR-046 п. 8 снял `data_revision` из `ProjectionState.is_ready`, после чего ранний выход
    «ничего не изменилось» (делегированный `is_ready`) перестал срабатывать: первая джоба
    писала состояние `ready`, и каждая следующая выходила из `_record_projection_state`,
    не записав ни состояние, ни ревизию своего источника. На живом графе это выглядело как
    «3 документа в графе, 1 в журнале» — то есть ось графа закрылась бы по ложной причине.
    """
    graph, vector = InMemoryGraphStore(), InMemoryVectorStore()
    state = InMemoryProjectionStateStore()
    analyzer, _ = build_analyzer(tmp_path, graph, vector, projection_state=state)

    for index, content in enumerate((CONTENT_1, CONTENT_2), start=1):
        src = tmp_path / f"d{index}.txt"
        src.write_text(content, encoding="utf-8")
        ctx = PipelineContext(
            job_id=f"j{index}",
            domain="it",
            doc_type="txt",
            source_url=f"src://d{index}.txt",
            source_path=str(src),
        )
        analyzer.run(ctx)

    revisions = state.source_revisions("it")
    assert set(revisions) == {"src://d1.txt", "src://d2.txt"}, (
        "каждый закоммиченный источник обязан получить строку журнала, иначе пораздельная "
        f"проверка не сможет отличить «нет ревизии» от «ревизия есть»: {revisions}"
    )
    config = os.environ.get("PROJECTION_CONFIG_FINGERPRINT", "default")
    for source_url, revision in revisions.items():
        expected = source_projection_revision(
            str(state.source_content_hashes("it")[source_url]), config
        )
        assert revision == expected
        chunk_ids = graph.list_chunk_ids_of_source(source_url, "it")
        assert chunk_ids, f"источник {source_url} обязан иметь чанки в графе"
        for chunk_id in chunk_ids:
            assert graph.get_node(chunk_id)["projection_revision"] == revision


def test_chunk_id_deterministic() -> None:
    assert _chunk_id("it", "src://d.txt", 0) == _chunk_id("it", "src://d.txt", 0)
    assert _chunk_id("it", "src://d.txt", 0) != _chunk_id("it", "src://d.txt", 1)
    assert _chunk_id("it", "src://d.txt", 0) != _chunk_id("library", "src://d.txt", 0)


class ReflectedEmbedder(Embedder):
    """Эмбеддер, возвращающий заданный вектор — M3.2: EMBED пишет вектор адаптера."""

    def __init__(self, vector: list[float]) -> None:
        self._vector = vector

    def embed(self, text: str, domain: str = "") -> list[float]:
        return list(self._vector)


def test_embed_stage_writes_injected_embedder_vector(tmp_path: Path) -> None:
    graph, vector = InMemoryGraphStore(), InMemoryVectorStore()
    bge_vector = [0.01 * i for i in range(1, 9)]
    analyzer, _ = build_analyzer(tmp_path, graph, vector, embedder=ReflectedEmbedder(bge_vector))
    src = tmp_path / "d.txt"
    src.write_text(CONTENT_1, encoding="utf-8")
    run_source(analyzer, src)

    chunk_ids = graph.list_chunk_ids_of_source("src://d.txt", "it")
    assert chunk_ids
    # L2-04: в хранилище лежит именно вектор инжектированного эмбеддера
    # (тест падает, если EmbedStage вернётся к детерминированному эмбеддеру).
    for chunk_id in chunk_ids:
        assert vector._vectors[chunk_id]["embedding"] == bge_vector


# --------------------------------------------------------------------------- A-2 (ADR-024)

class FailingVectorAxis(InMemoryVectorStore):
    """Разнородная пара (best_effort-контракт): вектор-ось падает на записи."""

    def __init__(self) -> None:
        super().__init__()
        self.fail = True

    def consistency_capability(self) -> str:
        return "best_effort"

    def upsert_vectors(self, items) -> None:
        if self.fail:
            raise RuntimeError("сбой записи векторов")
        super().upsert_vectors(items)


class _VariableChunker(Chunker):
    def chunk(self, text: str) -> list[str]:
        return [part for part in text.split("|") if part]


def test_commit_reindex_replaces_stale_context_provenance(tmp_path: Path) -> None:
    graph, vector = InMemoryGraphStore(), InMemoryVectorStore()
    analyzer, _ = build_analyzer(
        tmp_path,
        graph,
        vector,
        chunker=_VariableChunker(),
    )
    source = tmp_path / "d.txt"
    source.write_text("дедупликация|дедупликация", encoding="utf-8")
    run_source(analyzer, source)
    old_extra = {_chunk_id("it", "src://d.txt", 1)}
    context_node = graph.get_node(DEDUP_TAG_ID)
    assert context_node is not None
    assert old_extra.issubset(set(context_node["chunk_ids"]))
    assert (next(iter(old_extra)), DEDUP_TAG_ID, "MENTIONS") in graph._edges

    source.write_text("дедупликация", encoding="utf-8")
    run_source(analyzer, source)
    context_node = graph.get_node(DEDUP_TAG_ID)
    current_chunks = set(graph.list_chunk_ids_of_source("src://d.txt", "it"))
    assert context_node is not None
    assert context_node["_labels"] == ["ContextNode"]
    assert context_node["tag_id"] == DEDUP_TAG_ID
    assert context_node["source_ids"] == ["src://d.txt"]
    mentioned_chunks = {
        from_id
        for from_id, to_id, edge_type in graph._edges
        if to_id == DEDUP_TAG_ID and edge_type == "MENTIONS"
    }
    assert set(context_node["chunk_ids"]) == current_chunks
    assert mentioned_chunks == current_chunks
    assert not old_extra.intersection(mentioned_chunks)


class FailOnSecondUpsertVector(InMemoryVectorStore):
    """Второй прогон (re-index) падает на upsert_vectors: имитирует сбой второй оси.

    Первый прогон проходит успешно, что позволяет проверить компенсацию при re-index.
    """

    def __init__(self) -> None:
        super().__init__()
        self._call_count = 0

    def consistency_capability(self) -> str:
        return "best_effort"

    def upsert_vectors(self, items) -> None:
        self._call_count += 1
        if self._call_count == 2:
            raise RuntimeError("сбой векторов на втором прогоне (re-index)")
        super().upsert_vectors(items)


def test_best_effort_compensates_graph_on_vector_failure(tmp_path: Path) -> None:
    """A-2 (4.1): сбой второй оси в best_effort-паре → граф компенсирован,
    джоба failed с пометкой «компенсировано», ContextNode сохраняются."""
    graph, vector = InMemoryGraphStore(), FailingVectorAxis()
    assert not _is_atomic_pair_for_test(graph, vector)
    analyzer, _ = build_analyzer(tmp_path, graph, vector)
    src = tmp_path / "d.txt"
    src.write_text(CONTENT_1, encoding="utf-8")

    with pytest.raises(CommitStageError) as excinfo:
        run_source(analyzer, src)
    assert excinfo.value.compensated is True
    assert "компенсирован" in str(excinfo.value)

    # чанки компенсированы, context nodes сохранились (историчность, ADR-014);
    # якоря источника в схеме нет (ADR-046 п. 9)
    assert graph.list_chunk_ids_of_source("src://d.txt", "it") == []
    context_node = graph.get_node(DEDUP_TAG_ID)
    assert context_node is not None
    assert context_node["_labels"] == ["ContextNode"]
    assert context_node["tag_id"] == DEDUP_TAG_ID
    assert context_node["source_ids"] == []
    assert context_node["chunk_ids"] == []
    assert not any(edge_type == "MENTIONS" for _, _, edge_type in graph._edges)
    assert not vector._vectors


def test_node_counter_stays_empty_when_a_best_effort_pair_is_compensated(tmp_path: Path) -> None:
    """Счётчик нод пуст, когда проекция была скомпенсирована (ADR-024 + ADR-049 п. 4).

    Важная поправка к формулировке: ноды `ContextNode` при компенсации **выживают** в графе
    (см. `test_best_effort_compensates_graph_on_vector_failure` выше). Значит счётчик
    описывает не «что лежит в графе», а «что эта джоба записала как свою проекцию» и не
    откатила. Здесь проекция откачена, поэтому считать нечего, а `graph_projection_status`
    это уже сообщает.

    Тест не пустой по построению: ноды в графе есть (проверяется), сущности от модели
    получены, и всё же счётчик пуст - потому что запись не состоялась.
    """
    graph, vector = InMemoryGraphStore(), FailingVectorAxis()
    analyzer, _ = build_analyzer(tmp_path, graph, vector)
    src = tmp_path / "compensated.txt"
    src.write_text(CONTENT_1, encoding="utf-8")
    ctx = PipelineContext(
        job_id="j",
        domain="it",
        doc_type="txt",
        source_url="src://compensated.txt",
        source_path=str(src),
        tags=[],
        links=[],
        metadata={},
    )

    with pytest.raises(CommitStageError) as excinfo:
        analyzer.run(ctx)
    assert excinfo.value.compensated is True

    # Ненулевая величина, ради которой тест не пуст: нода в графе пережила компенсацию.
    survivors = {
        node_id: node
        for node_id in graph._nodes
        if (node := graph.get_node(node_id)) and node.get("extractor_version")
    }
    assert survivors, "ожидалась хотя бы одна помеченная нода: тест обязан быть непустым"
    assert ctx.graph_projection_status != "committed", ctx.graph_projection_status
    assert ctx.nodes_by_extractor_version == {}, ctx.nodes_by_extractor_version


def _is_atomic_pair_for_test(graph: InMemoryGraphStore, vector: InMemoryVectorStore) -> bool:
    return (
        graph.consistency_capability() == "atomic"
        and vector.consistency_capability() == "atomic"
        and graph.engine_key() == vector.engine_key()
    )


def test_best_effort_rerun_after_failure_replays_commit(tmp_path: Path) -> None:
    """A-2 (4.3, L2-06): failed COMMIT не оставляет active no-op; retry повторяет запись."""
    graph, vector = InMemoryGraphStore(), FailingVectorAxis()
    analyzer, registry = build_analyzer(tmp_path, graph, vector)
    src = tmp_path / "d.txt"
    src.write_text(CONTENT_1, encoding="utf-8")

    with pytest.raises(CommitStageError):
        run_source(analyzer, src)
    assert registry.latest_active("it", "src://d.txt") is None

    vector.fail = False
    ctx = run_source(analyzer, src)
    assert ctx.commit_applied is True
    assert graph.list_chunk_ids_of_source("src://d.txt", "it")
    assert registry.latest_active("it", "src://d.txt") is not None


def test_best_effort_reindex_compensates_stale_vectors(tmp_path: Path) -> None:
    """A-2 (4.1 + L2-03): re-index — первый прогон успешен, второй падает на векторах.

    Компенсация должна удалить чанки ИЗ ГРАФА И ИЗ ВЕКТОРА (старый stale-vector
    не откатывается транзакцией, если delete_vectors ещё не записался — BUG #1 ревьюера).
    """
    vector = FailOnSecondUpsertVector()
    graph = InMemoryGraphStore()
    analyzer, registry = build_analyzer(tmp_path, graph, vector)
    src = tmp_path / "d.txt"

    # --- первый прогон: CONTENT_1, обе оси записаны ---
    src.write_text(CONTENT_1, encoding="utf-8")
    run_source(analyzer, src)
    v1 = registry.latest_active("it", "src://d.txt")
    assert v1 is not None and v1["version"] == 1
    chunk_ids_v1 = graph.list_chunk_ids_of_source("src://d.txt", "it")
    assert len(chunk_ids_v1) > 0, "чанки записаны в граф"
    assert len(vector._vectors) > 0, "эмбеддинги записаны в вектор"

    # --- второй прогон: CONTENT_2, re-index → вектор падает → компенсация обеих осей ---
    src.write_text(CONTENT_2, encoding="utf-8")
    with pytest.raises(CommitStageError) as excinfo:
        run_source(analyzer, src)
    assert excinfo.value.compensated is True

    # граф пуст (чанки удалены)
    assert graph.list_chunk_ids_of_source("src://d.txt", "it") == []
    # ВЕКТОР тОЖЕ пуст: орфанов нет (L2-03)
    assert not vector._vectors, "старые эмбеддинги удалены при компенсации"
    # context node сохранился (историчность, ADR-014); якоря источника нет (ADR-046 п. 9)
    context_node = graph.get_node(DEDUP_TAG_ID)
    assert context_node is not None
    assert context_node["_labels"] == ["ContextNode"]
    assert context_node["tag_id"] == DEDUP_TAG_ID
    assert context_node["source_ids"] == []
    assert context_node["chunk_ids"] == []
    assert not any(edge_type == "MENTIONS" for _, _, edge_type in graph._edges)
    v2 = registry.latest_active("it", "src://d.txt")
    assert v2 is None

    # --- третий прогон: CONTENT_2 после rollback — повторяет успешную запись ---
    run_source(analyzer, src)
    assert registry.latest_active("it", "src://d.txt")["version"] == 2
    assert graph.list_chunk_ids_of_source("src://d.txt", "it")
    assert vector._vectors


def test_atomic_pair_writes_both_axes_in_one_batch(tmp_path: Path) -> None:
    """A-2 (4.2/2.2): атомарная пара — обе оси через единый batch движка
    (Neo4j-контракт `atomic_batch`), без компенсации."""
    vector_axis = InMemoryVectorStore()
    graph = _AtomicBatchGraph(vector_axis)
    assert graph.consistency_capability() == vector_axis.consistency_capability() == "atomic"
    assert graph.engine_key() == vector_axis.engine_key()

    analyzer, _ = build_analyzer(tmp_path, graph, vector_axis)
    src = tmp_path / "d.txt"
    src.write_text(CONTENT_1, encoding="utf-8")
    run_source(analyzer, src)

    chunk_ids = graph.list_chunk_ids_of_source("src://d.txt", "it")
    assert chunk_ids, "обе оси записаны через единый batch"
    # в единой транзакции лежат операции ОБЕИХ осей (граф: узлы+рёбра; вектор: delete+upsert)
    kinds = {kind for kind, _ in graph.batch_calls}
    assert {"upsert_nodes", "upsert_edges", "upsert_vectors"} <= kinds
    assert set(vector_axis._vectors) == set(chunk_ids), "оси согласованы после атомарного COMMIT"
    # компенсация не вызывалась: batch — единственный путь записи
    assert graph.delete_node_outside_tx == 0


class _AtomicBatchGraph(InMemoryGraphStore):
    """Граф-координатор атомарной пары: единая запись обеих осей (контракт A-2).

    Имитирует Neo4j-pair: `atomic_batch()` возвращает общий контекст, через который
    идут операции и графовой, и векторной осей (в прототипе — InMemory-делегирование).
    """

    def __init__(self, vector_axis: InMemoryVectorStore) -> None:
        super().__init__()
        self._vector_axis = vector_axis
        self.batch_calls: list[tuple[str, Any]] = []
        self.delete_node_outside_tx = 0

    @contextmanager
    def atomic_batch(self) -> Iterator[_RecordingBatch]:
        # упрощённая модель: без транзакционного отката внутри батча (в реальном Neo4j
        # его делает session/begin_transaction); исключение просто пробрасывается
        yield _RecordingBatch(self, self._vector_axis)

    def delete_node(self, node_id: str) -> bool:
        if self._journal is None:
            self.delete_node_outside_tx += 1
        return super().delete_node(node_id)


class _RecordingBatch:
    """Единый контекст обеих осей (A-2): логирует и делегирует операции в оси."""

    def __init__(self, graph: InMemoryGraphStore, vector: InMemoryVectorStore) -> None:
        self._graph = graph
        self._vector = vector

    def _record(self, kind: str, arg: Any) -> None:
        self._graph.batch_calls.append((kind, arg))

    def upsert_nodes(self, nodes: list[dict[str, Any]]) -> None:
        self._record("upsert_nodes", nodes)
        self._graph.upsert_nodes(nodes)

    def upsert_edges(self, edges: list[dict[str, Any]]) -> None:
        self._record("upsert_edges", edges)
        self._graph.upsert_edges(edges)

    def delete_node(self, node_id: str) -> bool:
        self._record("delete_node", node_id)
        return self._graph.delete_node(node_id)

    def upsert_vectors(self, items: list[dict[str, Any]]) -> None:
        self._record("upsert_vectors", items)
        self._vector.upsert_vectors(items)

    def delete_vectors(self, chunk_ids: list[str]) -> None:
        self._record("delete_vectors", chunk_ids)
        self._vector.delete_vectors(chunk_ids)

    def remove_source_from_entities(
        self,
        domain: str,
        source_url: str,
        chunk_ids: list[str],
    ) -> None:
        self._record(
            "remove_source",
            {"domain": domain, "source_url": source_url, "chunk_ids": chunk_ids},
        )
        self._graph.remove_source_from_entities(domain, source_url, chunk_ids)


# ---------------------------------------------------------------- ADR-028 (S2) retry

class _TransientFakeStore:
    """Фейк-хранилище: заданное число transient-сбоев, затем успех (2.5.1)."""

    def __init__(self, fail_transient: int, *, transient: bool = True) -> None:
        self._fail = fail_transient
        self.calls = 0
        self.transient = transient

    def transient_aware(self) -> bool:
        return True

    def is_transient(self, exc: BaseException) -> bool:
        return self.transient

    def op(self) -> str:
        self.calls += 1
        if self.calls <= self._fail:
            raise RuntimeError("Neo.TransientError.Transaction.DeadlockDetected")
        return "ok"


class _NonAwareStore:
    """Хранилище без декларации transient-возможности (дефолт ABC)."""

    def __init__(self) -> None:
        self.calls = 0

    def op(self) -> str:
        self.calls += 1
        raise RuntimeError("boom")


def test_commit_retry_transient_until_success() -> None:
    """2.5.1: transient-ошибки ретраятся; успех на N+1-й попытке."""
    store = _TransientFakeStore(fail_transient=2)
    result = _with_commit_retry([store], store.op, attempts=5, base_delay=0.0, jitter=0.0)
    assert result == "ok"
    assert store.calls == 3, "повтор до успеха: N transient + 1 успешная"


def test_commit_retry_exhausts_attempts() -> None:
    """2.5.1: transient-ошибки исчерпывают попытки → raise, вызовов == attempts."""
    store = _TransientFakeStore(fail_transient=99)
    with pytest.raises(RuntimeError):
        _with_commit_retry([store], store.op, attempts=4, base_delay=0.0, jitter=0.0)
    assert store.calls == 4


def test_commit_retry_non_transient_no_retry() -> None:
    """2.5.1: не-transient ошибка → failed без повторов."""
    store = _TransientFakeStore(fail_transient=1, transient=False)
    with pytest.raises(RuntimeError):
        _with_commit_retry([store], store.op, attempts=5, base_delay=0.0, jitter=0.0)
    assert store.calls == 1


def test_commit_retry_ignores_store_without_declaration() -> None:
    """2.5.1: хранилище без transient_aware() считается неповторимым."""
    store = _NonAwareStore()
    with pytest.raises(RuntimeError):
        _with_commit_retry([store], store.op, attempts=3, base_delay=0.0, jitter=0.0)
    assert store.calls == 1


def test_calls_recorded_empty() -> None:
    """Заглушка против пустых записей в _RecordingBatch (mypy/покрытие)."""
    graph = _AtomicBatchGraph(InMemoryVectorStore())
    assert graph.batch_calls == []


def test_commit_plan_nodes_and_edges_sorted(tmp_path: Path) -> None:
    """2.5.2 (ADR-028): порядок записи детерминирован — nodes по node_id,
    edges по (from_id, to_id, type) — защита от deadlock-циклов."""
    vector_axis = InMemoryVectorStore()
    graph = _AtomicBatchGraph(vector_axis)
    analyzer, _ = build_analyzer(tmp_path, graph, vector_axis)
    src = tmp_path / "d.txt"
    src.write_text("зигзаг алгоритм дедупликация кэш рёбра граф индекс данные\n", encoding="utf-8")
    run_source(analyzer, src)

    kinds = {kind for kind, _ in graph.batch_calls}
    assert "upsert_nodes" in kinds and "upsert_edges" in kinds

    nodes = next(arg for kind, arg in graph.batch_calls if kind == "upsert_nodes")
    node_ids = [n["node_id"] for n in nodes]
    assert node_ids == sorted(node_ids), "узлы пишутся в детерминированном порядке (node_id)"

    edges = next(arg for kind, arg in graph.batch_calls if kind == "upsert_edges")
    edge_keys = [(e["from_id"], e["to_id"], e["type"]) for e in edges]
    assert edge_keys == sorted(edge_keys), "рёбра пишутся в детерминированном порядке"