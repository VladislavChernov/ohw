"""Query Worker (graphrag-query-worker): claim -> run -> publish.

Читает Task Queue независимо от HTTP-подписчика SSE (ADR-023): разрыв соединения
клиента не отменяет задачу. Отменённые до старта задачи пропускаются (claim).

Переключение адаптеров на лету (M3, add-topology-adapters): при заданной
`TOPOLOGY_URL` воркер стартует на карте топологии, затем опрашивает revision
интервалом `TOPOLOGY_POLL_INTERVAL` (дефолт 5 с; 0 — без hot-reload) и, при
изменении, пересобирает QueryPipeline без рестарта контейнера.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from typing import Any

from graphrag_proto.query_service.store import TaskStore
from graphrag_proto.query_service.task_queue import TaskQueue
from graphrag_proto.retrieval.adapters.topology_client import TopologyClient
from graphrag_proto.retrieval.pipeline import QueryPipeline

_LOG = logging.getLogger("graphrag_proto.query_service.worker")


class QueryWorker:
    def __init__(
        self,
        queue: TaskQueue,
        store: TaskStore,
        pipeline: QueryPipeline,
        worker_id: str = "worker-1",
        poll_interval_s: float = 5.0,
        pipeline_rebuilder: Callable[[], None] | None = None,
        backoff_base_s: float = 1.0,
        backoff_max_s: float = 30.0,
        reclaim_timeout_s: float = 60.0,
        reclaim_interval_s: float = 10.0,
        metrics_interval_s: float = 30.0,
    ) -> None:
        self._queue = queue
        self._store = store
        self._pipeline = pipeline
        self._worker_id = worker_id
        self._poll_interval_s = max(0.0, poll_interval_s)
        self._pipeline_rebuilder = pipeline_rebuilder if self._poll_interval_s > 0 else None
        self._backoff_base_s = max(0.1, backoff_base_s)
        self._backoff_max_s = max(self._backoff_base_s, backoff_max_s)
        self._reclaim_timeout_s = max(1.0, reclaim_timeout_s)
        self._reclaim_interval_s = max(0.0, reclaim_interval_s)
        self._metrics_interval_s = max(0.0, metrics_interval_s)

    def set_pipeline(self, pipeline: QueryPipeline) -> None:
        """Замена пайплайна после hot-reload (вызывается между итерациями цикла).

        Каждый пайплайн владеет своим общим executor'ом — старый пул закрывается,
        чтобы не копить потоки при частых ребилдах топологии.
        """
        old = self._pipeline
        self._pipeline = pipeline
        shutdown = getattr(old, "shutdown", None)
        if callable(shutdown):
            shutdown()

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
        next_reclaim = 0.0
        next_metrics = 0.0
        failures = 0
        topology_failures = 0
        while True:
            if self._pipeline_rebuilder is not None and time.monotonic() >= next_poll:
                try:
                    self._pipeline_rebuilder()
                    topology_failures = 0
                except Exception:  # noqa: BLE001 - опрос топологии не роняет воркер
                    topology_failures += 1
                    _LOG.error(
                        "topology poll failed (%d consecutive)", topology_failures, exc_info=True
                    )
                next_poll = time.monotonic() + self._poll_interval_s
            if self._reclaim_interval_s > 0 and time.monotonic() >= next_reclaim:
                try:
                    self._queue.reclaim(self._worker_id, min_idle_s=self._reclaim_timeout_s)
                except Exception:  # noqa: BLE001 - reclaim терпим к транспорту
                    _LOG.warning("reclaim failed", exc_info=True)
                next_reclaim = time.monotonic() + self._reclaim_interval_s
            if self._metrics_interval_s > 0 and time.monotonic() >= next_metrics:
                self._log_metrics_snapshot(topology_failures)
                next_metrics = time.monotonic() + self._metrics_interval_s
            process = False
            try:
                process = self.process_one()
                failures = 0
            except KeyboardInterrupt:
                return
            except Exception:  # noqa: BLE001 - транспорт/бэкенд: backoff вместо tight loop
                failures += 1
                delay = min(self._backoff_max_s, self._backoff_base_s * (2 ** (failures - 1)))
                _LOG.warning(
                    "worker %s: transport/backend failure #%d, backoff %.1fs",
                    self._worker_id,
                    failures,
                    delay,
                    exc_info=True,
                )
                time.sleep(delay)
                continue
            if not process:
                time.sleep(idle_sleep_s)

    def _log_metrics_snapshot(self, topology_poll_errors_total: int) -> None:
        """Этап A метрик: JSON-строка в лог (structlog/Loki-совместимый формат)."""
        payload: dict[str, Any] = {
            "domain": "*",
            "topology_poll_errors_total": topology_poll_errors_total,
        }
        try:
            payload["queue_depth"] = self._queue.depth()
        except Exception:  # noqa: BLE001 - снапшот не роняет воркер
            payload["queue_depth"] = -1
        cache = getattr(self._pipeline, "_semantic_cache", None)
        if cache is not None:
            try:
                stats = cache.stats()
                payload["cache_entries"] = stats.get("entries", 0)
                payload["cache_hits"] = stats.get("hits", 0)
                payload["cache_misses"] = stats.get("misses", 0)
            except Exception:  # noqa: BLE001 - снапшот не роняет воркер
                _LOG.warning("metrics snapshot: cache stats недоступны", exc_info=True)
        _LOG.info("trigger_metrics snapshot %s", json.dumps(payload, ensure_ascii=False))


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
        _LOG.info("topology revision %s: пересборка pipeline без рестарта", revision)
        on_reload(client.adapters_map())

    return rebuild


def main() -> None:
    from graphrag_proto.query_service.runtime import (
        build_pipeline,
        build_queue,
        build_semantic_cache,
        build_store,
    )
    from graphrag_proto.security import install_redaction

    install_redaction()
    queue = build_queue()
    store = build_store()
    worker_id = os.environ.get("QUERY_WORKER_ID", "worker-1")

    # Semantic Cache (бандл 3/3): собирается один раз и переживает hot-reload —
    # объект общий для initial-сборки и пересборок пайплайна (записи сохраняются).
    semantic_cache = build_semantic_cache()

    client = TopologyClient.from_env()
    initial_map = client.adapters_map() if client is not None else None
    pipeline = build_pipeline(adapter_map=initial_map, semantic_cache=semantic_cache)

    poll_interval_s = float(os.environ.get("TOPOLOGY_POLL_INTERVAL", "5"))
    worker = QueryWorker(
        queue=queue,
        store=store,
        pipeline=pipeline,
        worker_id=worker_id,
        poll_interval_s=poll_interval_s,
        backoff_base_s=float(os.environ.get("WORKER_BACKOFF_BASE_S", "1")),
        backoff_max_s=float(os.environ.get("WORKER_BACKOFF_MAX_S", "30")),
        reclaim_timeout_s=float(os.environ.get("TASK_RECLAIM_TIMEOUT_S", "60")),
        reclaim_interval_s=float(os.environ.get("TASK_RECLAIM_INTERVAL_S", "10")),
        metrics_interval_s=float(os.environ.get("METRICS_SNAPSHOT_INTERVAL_S", "30")),
        pipeline_rebuilder=_topology_rebuilder(
            client,
            on_reload=lambda adapter_map: worker.set_pipeline(
                build_pipeline(adapter_map=adapter_map, semantic_cache=semantic_cache)
            ),
        )
        if client is not None and poll_interval_s > 0
        else None,
    )
    worker.loop()


if __name__ == "__main__":
    main()