"""Hot-reload: опрос revision через _topology_rebuilder, замена пайплайна в цикле."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from graphrag_proto.query_service.store import TaskStore
from graphrag_proto.query_service.task_queue import InMemoryTaskQueue
from graphrag_proto.query_service.worker import QueryWorker, _topology_rebuilder


class FakeTopo:
    """Модель TopologyClient: текущий кэш + результаты очередных поллов refresh.

    `revision(refresh=False)` — кэш (старт); `revision(refresh=True)` — следующий
    полл: применяет снимок `(revision, map)` из заполненного сервером списка.
    """

    def __init__(
        self,
        current_rev: int | None,
        current_map: dict[str, str],
        polls: list[tuple[int | None, dict[str, str]]],
    ) -> None:
        self._current_rev = current_rev
        self._current_map = current_map
        self._polls = list(polls)
        self._pos = 0

    def revision(self, refresh: bool = False) -> int | None:
        if refresh and self._pos < len(self._polls):
            rev, mapping = self._polls[self._pos]
            self._pos += 1
            self._current_rev, self._current_map = rev, mapping
        return self._current_rev

    def adapters_map(self) -> dict[str, str] | None:
        return self._current_map


def test_rebuilder_reloads_on_revision_change_skip_same() -> None:
    map_a = {"vector_store": "inmemory"}
    map_b = {"vector_store": "neo4j"}
    map_c = {"graph_store": "inmemory"}
    # Старт rev=5; поллы: без изменения, 5->7 (сброс), 7->7, 7->8 (сброс)
    topo = FakeTopo(5, map_a, [(5, map_a), (7, map_b), (7, map_b), (8, map_c)])
    reloaded: list[dict[str, str] | None] = []
    rebuild = _topology_rebuilder(topo, on_reload=reloaded.append)

    rebuild()  # 5 -> 5: без изменений
    rebuild()  # 5 -> 7: пересборка
    rebuild()  # 7 -> 7: без изменений
    rebuild()  # 7 -> 8: пересборка
    assert reloaded == [map_b, map_c]


def test_rebuilder_ignores_unreachable_topology() -> None:
    map_neo4j = {"vector_store": "neo4j"}
    # Старт rev=3; поллы: 3, недоступна, недоступна, 3->4 (сброс)
    topo = FakeTopo(3, {"vector_store": "inmemory"}, [(3, {"vector_store": "inmemory"}), (None, {}), (None, {}), (4, map_neo4j)])
    reloaded: list[dict[str, str] | None] = []
    rebuild = _topology_rebuilder(topo, on_reload=reloaded.append)
    rebuild()  # 3 -> 3: без изменений
    rebuild()  # 3 -> None: топология недоступна, сброса нет
    rebuild()  # None -> None: ещё недоступна
    rebuild()  # None -> 4: пересборка после восстановления топологии
    assert reloaded == [map_neo4j]


class _StopAfter(BaseException):
    """BaseException: не ловится `except Exception` в loop, управляемый выход из цикла."""


def test_loop_polls_rebuilder_and_swaps_pipeline(tmp_path: Path) -> None:
    calls = {"n": 0}
    worker_ref: dict[str, QueryWorker | None] = {"w": None}
    last_pipeline: dict[str, Any] = {"p": None}

    def rebuilder() -> None:
        calls["n"] += 1
        worker = worker_ref["w"]
        if worker is not None:
            worker.set_pipeline(f"P{calls['n']}")
            last_pipeline["p"] = worker._pipeline
        if calls["n"] >= 3:
            raise _StopAfter()

    worker = QueryWorker(
        queue=InMemoryTaskQueue(),
        store=TaskStore(tmp_path / "hot.sqlite"),
        pipeline="P0",
        poll_interval_s=0.001,
        pipeline_rebuilder=rebuilder,
        metrics_interval_s=0.0,  # отключаем снапшот чтобы не засорял лог
    )
    worker_ref["w"] = worker
    with pytest.raises(_StopAfter):
        worker.loop(idle_sleep_s=0.001)
    assert calls["n"] == 3
    assert last_pipeline["p"] == "P3"
    assert worker._pipeline == "P3"


def test_reclaim_timeout_cannot_be_shorter_than_llm_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LLM_TIMEOUT_S", "120")
    worker = QueryWorker(
        queue=InMemoryTaskQueue(),
        store=TaskStore(tmp_path / "reclaim.sqlite"),
        pipeline=object(),
        poll_interval_s=0.0,
        metrics_interval_s=0.0,
    )

    assert worker._reclaim_timeout_s >= 150.0


def test_poll_interval_zero_disables_rebuilder(tmp_path: Path) -> None:
    def rebuilder() -> None:  # pragma: no cover - не должен вызываться
        raise AssertionError("rebuilder вызван при poll_interval_s=0")

    worker = QueryWorker(
        queue=InMemoryTaskQueue(),
        store=TaskStore(tmp_path / "off.sqlite"),
        pipeline=object(),
        poll_interval_s=0,
        pipeline_rebuilder=rebuilder,
        metrics_interval_s=0.0,
    )
    assert worker._pipeline_rebuilder is None


def test_build_pipeline_preserves_semantic_cache_object() -> None:
    """Бандл 3/3: build_pipeline с semantic_cache= переживает hot-reload — объект общий."""
    from graphrag_proto.query_service.runtime import build_pipeline
    from graphrag_proto.retrieval.semantic_cache import InMemorySemanticCache

    cache = InMemorySemanticCache(threshold=0.9, ttl_s=300)
    p1 = build_pipeline(adapter_map=None, semantic_cache=cache)
    p2 = build_pipeline(adapter_map=None, semantic_cache=cache)
    # один и тот же объект — записи сохраняются между пересборками
    assert p1._semantic_cache is p2._semantic_cache is cache


def test_loop_exponential_backoff_on_transport_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from graphrag_proto.query_service.models import Task

    delays: list[float] = []
    clock = [0.0]
    # Фиксированная шкала времени: поллер (time.monotonic) и backoff (time.sleep)
    # движутся на одной виртуальной оси — тест детерминирован на любой скорости CPU.
    monkeypatch.setattr("time.monotonic", lambda: clock[0])
    monkeypatch.setattr(
        "time.sleep", lambda delay: (clock.__setitem__(0, clock[0] + delay), delays.append(float(delay)))
    )

    class _StopAfter(BaseException):
        """Выход из loop через rebuilder (не ловится `except Exception`)."""

    class FailingQueue(InMemoryTaskQueue):
        def claim(self, worker_id: str, timeout_s: float = 1.0) -> Task | None:
            raise RuntimeError("transport down")

    calls = {"n": 0}

    def rebuilder() -> None:
        calls["n"] += 1
        if calls["n"] >= 4:
            raise _StopAfter()

    worker = QueryWorker(
        queue=FailingQueue(),
        store=TaskStore(tmp_path / "backoff.sqlite"),
        pipeline=object(),
        poll_interval_s=0.001,
        pipeline_rebuilder=rebuilder,
        backoff_base_s=1.0,
        backoff_max_s=30.0,
        reclaim_interval_s=0.0,
        metrics_interval_s=0.0,
    )
    with pytest.raises(_StopAfter):
        worker.loop(idle_sleep_s=0.001)
    assert delays == [1.0, 2.0, 4.0]


def test_loop_counts_topology_poll_failures(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    calls = {"n": 0}

    def rebuilder() -> None:
        calls["n"] += 1
        if calls["n"] == 3:
            raise _StopAfter()
        raise RuntimeError("topology down")

    worker = QueryWorker(
        queue=InMemoryTaskQueue(),
        store=TaskStore(tmp_path / "topo_fail.sqlite"),
        pipeline=object(),
        poll_interval_s=0.001,
        pipeline_rebuilder=rebuilder,
        reclaim_interval_s=0.0,
        metrics_interval_s=0.0,
    )
    with caplog.at_level("ERROR", logger="graphrag_proto.query_service.worker"), pytest.raises(
        _StopAfter
    ):
        worker.loop(idle_sleep_s=0.001)
    records = [r for r in caplog.records if "topology poll failed" in r.getMessage()]
    assert [r.getMessage() for r in records] == [
        "topology poll failed (1 consecutive)",
        "topology poll failed (2 consecutive)",
    ]


def test_snapshot_fields_present(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    from graphrag_proto.retrieval.semantic_cache import InMemorySemanticCache

    calls = {"n": 0}
    cache = InMemorySemanticCache(threshold=0.85, ttl_s=0)

    class _StopAfter(BaseException):
        pass

    def rebuilder() -> None:
        calls["n"] += 1
        if calls["n"] >= 2:
            raise _StopAfter()

    worker = QueryWorker(
        queue=InMemoryTaskQueue(),
        store=TaskStore(tmp_path / "snap.sqlite"),
        pipeline=type("_Pipeline", (), {"_semantic_cache": cache})(),
        poll_interval_s=0.001,
        pipeline_rebuilder=rebuilder,
        reclaim_interval_s=0.0,
        metrics_interval_s=0.001,
    )
    from graphrag_proto.query_service.models import Task

    worker._queue.submit(Task(task_id="q_snap1", domain="it", query="test query"))
    with caplog.at_level("INFO", logger="graphrag_proto.query_service.worker"), pytest.raises(
        _StopAfter
    ):
        worker.loop(idle_sleep_s=0.001)
    snapshot_msgs = [r.getMessage() for r in caplog.records if "trigger_metrics snapshot" in r.getMessage()]
    assert len(snapshot_msgs) >= 1
    import json

    snapshot = json.loads(snapshot_msgs[-1].split("trigger_metrics snapshot ", 1)[1])
    assert "topology_poll_errors_total" in snapshot
    assert snapshot["topology_poll_errors_total"] == 0
    assert snapshot["queue_depth"] == 1
    assert snapshot["domain"] == "*"


def test_poll_error_counter_in_snapshot(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    calls = {"n": 0}
    snap_data: list[dict[str, Any]] = []

    class _StopAfter(BaseException):
        pass

    def rebuilder() -> None:
        calls["n"] += 1
        if calls["n"] >= 3:
            raise _StopAfter()
        raise RuntimeError("topology down")

    worker = QueryWorker(
        queue=InMemoryTaskQueue(),
        store=TaskStore(tmp_path / "snap_err.sqlite"),
        pipeline=object(),
        poll_interval_s=0.001,
        pipeline_rebuilder=rebuilder,
        reclaim_interval_s=0.0,
        metrics_interval_s=0.001,
    )
    import json

    with caplog.at_level("INFO", logger="graphrag_proto.query_service.worker"), pytest.raises(
        _StopAfter
    ):
        worker.loop(idle_sleep_s=0.001)
    for msg in (r.getMessage() for r in caplog.records if "trigger_metrics snapshot" in r.getMessage()):
        snap_data.append(json.loads(msg.split("trigger_metrics snapshot ", 1)[1]))
    assert len(snap_data) >= 1
    assert snap_data[-1]["topology_poll_errors_total"] == 2