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
    )
    worker_ref["w"] = worker
    with pytest.raises(_StopAfter):
        worker.loop(idle_sleep_s=0.001)
    assert calls["n"] == 3
    assert last_pipeline["p"] == "P3"
    assert worker._pipeline == "P3"


def test_poll_interval_zero_disables_rebuilder(tmp_path: Path) -> None:
    def rebuilder() -> None:  # pragma: no cover - не должен вызываться
        raise AssertionError("rebuilder вызван при poll_interval_s=0")

    worker = QueryWorker(
        queue=InMemoryTaskQueue(),
        store=TaskStore(tmp_path / "off.sqlite"),
        pipeline=object(),
        poll_interval_s=0,
        pipeline_rebuilder=rebuilder,
    )
    assert worker._pipeline_rebuilder is None