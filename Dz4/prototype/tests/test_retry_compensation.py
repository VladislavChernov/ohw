"""Unit-тесты ADR-028 (S2): retry transient-ошибок COMMIT и момент компенсации.

Покрывают задачи 2.5.1 и 2.5.3 бандла `concurrent-ingest-write-policy`:
- `_with_commit_retry`: transient → повторы, non-transient → без повторов,
  число попыток = `N_RETRY_COMMIT + 1` (N_RETRY_COMMIT — число повторов);
- best_effort: сбой второй оси ретраится, компенсация — только на окончательном
  отказе (UC12-02: не на первом transient), ошибка несёт `compensated=True`
  и сохраняет причину через `__cause__`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from graphrag_proto.ingestion_service.pipeline.orchestrator import (
    N_RETRY_COMMIT,
    CommitStage,
    CommitStageError,
    _compensate,
    _parse_retry_env,
    _with_commit_retry,
)
from graphrag_proto.retrieval.adapters.inmemory import InMemoryGraphStore, InMemoryVectorStore


class TransientError(Exception):
    """Маркер transient-ошибки движка (аналог `neo4j.exceptions.TransientError`)."""


class FakeTransientVector(InMemoryVectorStore):
    """Вектор-ось с декларацией transient-способности (S2-контракт провайдера).

    Падает `fail_times` раз подряд, затем делегирует базовой InMemory-реализации.
    """

    def __init__(
        self,
        fail_times: int,
        exc_factory: Callable[[], BaseException] = lambda: TransientError("transient"),
    ) -> None:
        super().__init__()
        self.fail_times = fail_times
        self.calls = 0
        self._exc_factory = exc_factory

    def transient_aware(self) -> bool:
        return True

    def is_transient(self, exc: BaseException) -> bool:
        return isinstance(exc, TransientError)

    def upsert_vectors(self, items: list[dict[str, Any]]) -> None:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self._exc_factory()
        super().upsert_vectors(items)


class FakeTransientGraph(InMemoryGraphStore):
    """Графовая ось с декларацией transient-способности: падает `fail_times` раз."""

    def __init__(self, fail_times: int) -> None:
        super().__init__()
        self.fail_times = fail_times
        self.calls = 0

    def transient_aware(self) -> bool:
        return True

    def is_transient(self, exc: BaseException) -> bool:
        return isinstance(exc, TransientError)

    def upsert_nodes(self, nodes: list[dict[str, Any]]) -> None:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise TransientError("transient graph")
        super().upsert_nodes(nodes)


SOURCE_ID = "src:it:src://d.txt"
CHUNK_IDS = ["chk:a", "chk:b"]

NODES: list[dict[str, Any]] = [
    {"node_id": SOURCE_ID, "labels": ["Source"], "properties": {"source_url": "src://d.txt"}},
    *[
        {"node_id": chunk_id, "labels": ["Chunk"], "properties": {"text": chunk_id}}
        for chunk_id in CHUNK_IDS
    ],
]
EDGES: list[dict[str, Any]] = [
    {"from_id": SOURCE_ID, "to_id": chunk_id, "type": "CONTAINS", "properties": {}}
    for chunk_id in CHUNK_IDS
]
VECTORS: list[dict[str, Any]] = [
    {"chunk_id": chunk_id, "embedding": [0.1, 0.2], "metadata": {"text": chunk_id}}
    for chunk_id in CHUNK_IDS
]


def _write_best_effort(graph: InMemoryGraphStore, vector: InMemoryVectorStore) -> None:
    """Прогон best_effort-пути COMMIT с готовым планом записи (без registry/ctx)."""
    stage = CommitStage(registry=None, graph_store=graph, vector_store=vector)
    stage._write_best_effort(graph, vector, NODES, EDGES, VECTORS, [], list(CHUNK_IDS))


# --------------------------------------------------------------------- 2.5.1: retry-loop


def test_retry_repeats_transient_then_succeeds() -> None:
    """Transient-сбой дважды → два повтора → успех (всего 3 попытки)."""
    store = FakeTransientVector(fail_times=2)
    attempts = 0

    def fn() -> str:
        nonlocal attempts
        attempts += 1
        store.upsert_vectors(VECTORS)
        return "ok"

    assert _with_commit_retry([store], fn) == "ok"
    assert attempts == 3, f"ожидали 2 повтора после сбоя, получили попыток: {attempts}"


def test_no_retry_for_non_transient() -> None:
    """Non-transient ошибка → немедленный отказ, повторов нет."""
    store = FakeTransientVector(fail_times=99, exc_factory=lambda: RuntimeError("жёсткий сбой"))
    attempts = 0

    def fn() -> None:
        nonlocal attempts
        attempts += 1
        store.upsert_vectors(VECTORS)

    with pytest.raises(RuntimeError, match="жёсткий сбой"):
        _with_commit_retry([store], fn)
    assert attempts == 1, f"non-transient не должен ретраиться, попыток: {attempts}"


def test_attempts_equal_n_retry_commit_plus_one() -> None:
    """`N_RETRY_COMMIT` — число повторов: всего попыток N+1 (спека §2)."""
    store = FakeTransientVector(fail_times=99)
    attempts = 0

    def fn() -> None:
        nonlocal attempts
        attempts += 1
        store.upsert_vectors(VECTORS)

    with pytest.raises(TransientError):
        _with_commit_retry([store], fn, attempts=N_RETRY_COMMIT + 1)
    assert N_RETRY_COMMIT == 3, f"дефолт спеки — 3 повтора, в коде: {N_RETRY_COMMIT}"
    assert attempts == N_RETRY_COMMIT + 1, f"ожидали {N_RETRY_COMMIT + 1} попыток, {attempts}"


def test_zero_retries_single_attempt() -> None:
    """`N_RETRY_COMMIT=0` → ровно одна попытка, без повторов (спека §2)."""
    store = FakeTransientVector(fail_times=99)
    attempts = 0

    def fn() -> None:
        nonlocal attempts
        attempts += 1
        store.upsert_vectors(VECTORS)

    with pytest.raises(TransientError):
        _with_commit_retry([store], fn, attempts=0 + 1)
    assert attempts == 1, f"N_RETRY_COMMIT=0 даёт одну попытку, получено: {attempts}"


# ------------------------------------------------ 2.5.1-env: fail-fast при старте (UC12-03)


def test_parse_retry_env_defaults() -> None:
    """Дефолты спеки: N_RETRY_COMMIT=3, base=0.2, jitter=0.1."""
    assert _parse_retry_env({}) == (3, 0.2, 0.1)


def test_parse_retry_env_zero_retries_valid() -> None:
    """`N_RETRY_COMMIT=0` — валидная конфигурация (ровно одна попытка)."""
    assert _parse_retry_env({"N_RETRY_COMMIT": "0"}) == (0, 0.2, 0.1)


@pytest.mark.parametrize(
    "env",
    [
        {"N_RETRY_COMMIT": "-1"},
        {"N_RETRY_COMMIT": "abc"},
        {"N_RETRY_COMMIT": ""},
        {"RETRY_BASE_S": "-0.1"},
        {"RETRY_BASE_S": "не число"},
        {"RETRY_JITTER_S": "-2"},
        {"RETRY_JITTER_S": "nan?"},
    ],
)
def test_parse_retry_env_negative_or_non_numeric_fails_fast(env: dict[str, str]) -> None:
    """Отрицательные и нечисловые env-значения — ошибка конфигурации (fail-fast)."""
    with pytest.raises(ValueError):
        _parse_retry_env(env)


def test_commit_retry_attempts_below_one_rejected() -> None:
    """`attempts < 1` — ошибка валидации (N_RETRY_COMMIT >= 0 семантически)."""
    store = FakeTransientVector(fail_times=0)

    def fn() -> None:
        store.upsert_vectors(VECTORS)

    with pytest.raises(ValueError, match="attempts"):
        _with_commit_retry([store], fn, attempts=0)


# ------------------------------------------------- 2.5.3: момент компенсации best_effort


def test_best_effort_retries_transient_axis_without_compensation() -> None:
    """UC12-02: transient второй оси на 1-й попытке → повтор → успех без компенсации."""
    graph, vector = InMemoryGraphStore(), FakeTransientVector(fail_times=1)

    _write_best_effort(graph, vector)

    assert vector.calls == 2, "вторая ось обязана быть повторена"
    assert graph.list_chunk_ids_of_source(SOURCE_ID) == CHUNK_IDS, "граф записан"
    assert sorted(vector._vectors) == CHUNK_IDS, "вектор записан после повтора"
    assert graph.get_node(SOURCE_ID) is not None


def test_best_effort_compensates_after_exhausted_retries() -> None:
    """UC12-02: исчерпание повторов → компенсация обеих осей, `compensated=True`."""
    graph, vector = InMemoryGraphStore(), FakeTransientVector(fail_times=99)

    with pytest.raises(CommitStageError) as excinfo:
        _write_best_effort(graph, vector)

    assert excinfo.value.compensated is True
    assert "компенсирован" in str(excinfo.value)
    assert vector.calls == N_RETRY_COMMIT + 1, "компенсация — строго после исчерпания повторов"
    assert graph.list_chunk_ids_of_source(SOURCE_ID) == [], "чанки графа компенсированы"
    assert not vector._vectors, "орфанов в векторной оси нет (L2-03)"
    assert graph.get_node(SOURCE_ID) is not None, "Source сохраняется (ADR-014)"
    assert isinstance(excinfo.value.__cause__, TransientError), "причина сохранена (UC12-02)"


def test_best_effort_non_transient_second_axis_compensates_immediately() -> None:
    """Non-transient сбой второй оси: повторов нет, но оси всё равно выравниваются."""
    graph = InMemoryGraphStore()
    vector = FakeTransientVector(fail_times=99, exc_factory=lambda: RuntimeError("сбой вектора"))

    with pytest.raises(CommitStageError) as excinfo:
        _write_best_effort(graph, vector)

    assert excinfo.value.compensated is True
    assert vector.calls == 1, "non-transient не ретраится (спека §2)"
    assert graph.list_chunk_ids_of_source(SOURCE_ID) == []
    assert not vector._vectors
    assert isinstance(excinfo.value.__cause__, RuntimeError)


def test_best_effort_first_axis_transient_fails_without_compensation() -> None:
    """Transient-сбой ПЕРВОЙ оси (граф): повторов хватает/нет — компенсации нет.

    Спека §2 (UC12-02): сбой первой оси — джоба failed без компенсации,
    транзакция графа откатилась, векторная ось не трогалась. Ошибка поднимается
    как есть (transient), НЕ оборачиваясь в CommitStageError.
    """
    graph = FakeTransientGraph(fail_times=99)
    vector = InMemoryVectorStore()

    with pytest.raises(TransientError):
        _write_best_effort(graph, vector)

    assert graph.calls == N_RETRY_COMMIT + 1, "первая ось ретраится как transient"
    assert not vector._vectors, "векторная ось не затрагивалась (L2-03)"
    assert graph.get_node(SOURCE_ID) is None, "транзакция графа откатилась"


def test_compensate_removes_written_and_stale_chunks() -> None:
    """`_compensate` удаляет и записанные, и stale-чанки; Source остаётся."""
    graph = InMemoryGraphStore()
    vector = InMemoryVectorStore()
    with graph.transaction() as gx:
        gx.upsert_nodes(NODES)
        gx.upsert_edges(EDGES)
    vector.upsert_vectors(
        [
            {"chunk_id": "chk:a", "embedding": [0.1], "metadata": {}},
            {"chunk_id": "stale:x", "embedding": [0.2], "metadata": {}},
        ]
    )

    _compensate(graph, vector, stale_chunks=["stale:x"], written_chunk_ids=["chk:a", "chk:b"])

    assert graph.get_node("chk:a") is None
    assert graph.get_node("stale:x") is None
    assert graph.get_node(SOURCE_ID) is not None
    assert not vector._vectors, "векторы удалены, включая stale"