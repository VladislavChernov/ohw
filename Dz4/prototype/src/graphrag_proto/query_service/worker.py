"""Query Worker (graphrag-query-worker): claim -> run -> publish.

Читает Task Queue независимо от HTTP-подписчика SSE (ADR-023): разрыв соединения
клиента не отменяет задачу. Отменённые до старта задачи пропускаются (claim).
"""

from __future__ import annotations

import os
from typing import Any

from graphrag_proto.query_service.store import TaskStore
from graphrag_proto.query_service.task_queue import TaskQueue
from graphrag_proto.retrieval.pipeline import QueryPipeline


class QueryWorker:
    def __init__(self, queue: TaskQueue, store: TaskStore, pipeline: QueryPipeline, worker_id: str = "worker-1") -> None:
        self._queue = queue
        self._store = store
        self._pipeline = pipeline
        self._worker_id = worker_id

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
        while True:
            process = False
            try:
                process = self.process_one()
            except KeyboardInterrupt:
                return
            except Exception:  # noqa: BLE001 - очередь не должна ронять воркер
                process = True
            if not process:
                import time

                time.sleep(idle_sleep_s)


def main() -> None:
    from graphrag_proto.query_service.runtime import build_pipeline, build_queue, build_store

    worker = QueryWorker(
        queue=build_queue(),
        store=build_store(),
        pipeline=build_pipeline(),
        worker_id=os.environ.get("QUERY_WORKER_ID", "worker-1"),
    )
    worker.loop()


if __name__ == "__main__":
    main()