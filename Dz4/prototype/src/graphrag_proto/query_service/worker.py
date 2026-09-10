"""Query Worker (graphrag-query-worker): claim -> run -> publish.

Читает Task Queue независимо от HTTP-подписчика SSE (ADR-023): разрыв соединения
клиента не отменяет задачу. Отменённые до старта задачи пропускаются (claim).

Переключение адаптеров на лету (M3, add-topology-adapters): при заданной
`TOPOLOGY_URL` воркер стартует на карте топологии, затем опрашивает revision
интервалом `TOPOLOGY_POLL_INTERVAL` (дефолт 5 с; 0 — без hot-reload) и, при
изменении, пересобирает QueryPipeline без рестарта контейнера.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from typing import Any

from graphrag_proto.query_service.store import TaskStore
from graphrag_proto.query_service.task_queue import TaskQueue
from graphrag_proto.retrieval.adapters.topology_client import TopologyClient
from graphrag_proto.retrieval.pipeline import QueryPipeline


class QueryWorker:
    def __init__(
        self,
        queue: TaskQueue,
        store: TaskStore,
        pipeline: QueryPipeline,
        worker_id: str = "worker-1",
        poll_interval_s: float = 5.0,
        pipeline_rebuilder: Callable[[], None] | None = None,
    ) -> None:
        self._queue = queue
        self._store = store
        self._pipeline = pipeline
        self._worker_id = worker_id
        self._poll_interval_s = max(0.0, poll_interval_s)
        self._pipeline_rebuilder = pipeline_rebuilder if self._poll_interval_s > 0 else None

    def set_pipeline(self, pipeline: QueryPipeline) -> None:
        """Замена пайплайна после hot-reload (вызывается между итерациями цикла)."""
        self._pipeline = pipeline

    def process_one(self, idle_sleep_s: float = 0.2) -> bool:
        """Обработка одной задачи; False — очередь пуста (воркер может ждать)."""
        task = self._queue.claim(self._worker_id)
        if task is None:
            return False
        self._store.mark_running(task.task_id)

        def emit(event_type: str, payload: dict[str, Any]) -> None:
            self._queue.publish(task.task_id, event_type, payload)

        try:
            self._pipeline.run(task.query, task.domain or None, emit)
        except Exception as exc:  # noqa: BLE001 - разнородные сбои пайплайна
            emit("error", {"code": "pipeline_error", "message": str(exc)})
            self._store.mark_failed(task.task_id, str(exc))
            self._queue.ack(task.task_id, entry_id=task.entry_id)
            return True
        if self._queue.is_cancelled(task.task_id):
            self._store.mark_cancelled(task.task_id)
            self._queue.ack(task.task_id, entry_id=task.entry_id)
            return True
        self._store.mark_succeeded(task.task_id)
        self._queue.ack(task.task_id, entry_id=task.entry_id)
        return True

    def loop(self, idle_sleep_s: float = 0.2) -> None:
        next_poll = 0.0
        while True:
            if self._pipeline_rebuilder is not None and time.monotonic() >= next_poll:
                try:
                    self._pipeline_rebuilder()
                except Exception:  # noqa: BLE001, S110 - опрос топологии не роняет воркер
                    pass
                next_poll = time.monotonic() + self._poll_interval_s
            process = False
            try:
                process = self.process_one()
            except KeyboardInterrupt:
                return
            except Exception:  # noqa: BLE001 - очередь не должна ронять воркер
                process = True
            if not process:
                time.sleep(idle_sleep_s)


def _topology_rebuilder(
    client: TopologyClient,
    on_reload: Callable[[dict[str, str] | None], None],
) -> Callable[[], None]:
    """Замыкание опроса топологии: пересборка при смене revision.

    Терпимо к недоступности: revision=None топологии не шлёт сброса; как только
    топология снова отвечает с иной revision — пайплайн пересобирается.
    """
    last_revision: int | None = client.revision()

    def rebuild() -> None:
        nonlocal last_revision
        revision = client.revision(refresh=True)
        if revision is None or revision == last_revision:
            return
        last_revision = revision
        print(f"[topology] revision {revision}: пересборка pipeline без рестарта")
        on_reload(client.adapters_map())

    return rebuild


def main() -> None:
    from graphrag_proto.query_service.runtime import build_pipeline, build_queue, build_store

    queue = build_queue()
    store = build_store()
    worker_id = os.environ.get("QUERY_WORKER_ID", "worker-1")

    client = TopologyClient.from_env()
    initial_map = client.adapters_map() if client is not None else None
    pipeline = build_pipeline(adapter_map=initial_map)

    poll_interval_s = float(os.environ.get("TOPOLOGY_POLL_INTERVAL", "5"))
    worker = QueryWorker(
        queue=queue,
        store=store,
        pipeline=pipeline,
        worker_id=worker_id,
        poll_interval_s=poll_interval_s,
        pipeline_rebuilder=_topology_rebuilder(
            client,
            on_reload=lambda adapter_map: worker.set_pipeline(build_pipeline(adapter_map=adapter_map)),
        )
        if client is not None and poll_interval_s > 0
        else None,
    )
    worker.loop()


if __name__ == "__main__":
    main()