"""Query API (:8000): X-API-Key, 202/409/404, SSE stream через worker (8.2 smoke)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from graphrag_proto.ingestion_service.projection import (
    InMemoryProjectionStateStore,
    ProjectionState,
)
from graphrag_proto.query_service.app import create_app
from graphrag_proto.query_service.models import Task
from graphrag_proto.query_service.store import STATUS_CANCELLED, STATUS_QUEUED, TaskStore
from graphrag_proto.query_service.task_queue import InMemoryTaskQueue
from graphrag_proto.query_service.worker import QueryWorker
from graphrag_proto.retrieval.adapters.deterministic import DeterministicEmbedder
from graphrag_proto.retrieval.adapters.inmemory import InMemoryGraphStore, InMemoryVectorStore
from graphrag_proto.retrieval.adapters.llm import FakeLLM
from graphrag_proto.retrieval.adapters.reranker import NoOpRerankerAdapter
from graphrag_proto.retrieval.pipeline import QueryPipeline
from graphrag_proto.retrieval.profile import DomainProfileLoader

API_KEY = "test-api-key"


def make_app(tmp_path: Path) -> tuple[TestClient, InMemoryTaskQueue, TaskStore]:
    queue = InMemoryTaskQueue()
    store = TaskStore(tmp_path / "query_tasks.sqlite")
    client = TestClient(create_app(queue=queue, store=store, api_key=API_KEY))
    return client, queue, store


def headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


def make_worker(queue: InMemoryTaskQueue, store: TaskStore) -> QueryWorker:
    return QueryWorker(
        queue=queue,
        store=store,
        pipeline=QueryPipeline(
            embedder=DeterministicEmbedder(),
            graph_store=InMemoryGraphStore(),
            vector_store=InMemoryVectorStore(),
            reranker=NoOpRerankerAdapter(),
            llm=FakeLLM(text="Граф знаний связывает сущности."),
            profile_loader=DomainProfileLoader(),
        ),
    )


def test_health_without_key(tmp_path: Path) -> None:
    client, _, _ = make_app(tmp_path)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_auth_required_for_query(tmp_path: Path) -> None:
    client, _, _ = make_app(tmp_path)
    assert client.post("/query", json={"query": "x"}).status_code == 401
    assert client.post("/query", headers={"X-API-Key": "wrong"}, json={"query": "x"}).status_code == 401
    assert client.get("/query/tasks/q_0").status_code == 401
    assert client.delete("/query/tasks/q_0").status_code == 401
    assert client.get("/query/tasks/q_0/stream").status_code == 401


def test_post_query_202_and_queued(tmp_path: Path) -> None:
    client, queue, store = make_app(tmp_path)
    resp = client.post(
        "/query",
        headers=headers(),
        json={"query": "Что такое база данных?", "metadata": {"domain": "it"}},
    )
    assert resp.status_code == 202
    body = resp.json()
    task_id = body["task_id"]
    assert body["status"] == STATUS_QUEUED
    assert "accepted_at" in body
    stored = store.get(task_id)
    assert stored is not None and stored["domain"] == "it"
    assert any(t.task_id == task_id for t in queue._queue)


def test_post_query_validation_422(tmp_path: Path) -> None:
    client, _, _ = make_app(tmp_path)
    assert client.post("/query", headers=headers(), json={}).status_code == 422
    assert client.post("/query", headers=headers(), json={"query": ""}).status_code == 422
    assert (
        client.post("/query", headers=headers(), json={"query": "x", "metadata": {"domain": 42}}).status_code
        == 422
    )


def test_get_task_404(tmp_path: Path) -> None:
    client, _, _ = make_app(tmp_path)
    assert client.get("/query/tasks/nope", headers=headers()).status_code == 404


def test_cancel_lifecycle(tmp_path: Path) -> None:
    client, _, _ = make_app(tmp_path)
    task_id = client.post("/query", headers=headers(), json={"query": "вопрос"}).json()["task_id"]

    resp = client.delete(f"/query/tasks/{task_id}", headers=headers())
    assert resp.status_code == 200
    assert resp.json()["status"] == STATUS_CANCELLED
    assert client.delete(f"/query/tasks/{task_id}", headers=headers()).status_code == 409
    assert client.get(f"/query/tasks/{task_id}", headers=headers()).json()["status"] == STATUS_CANCELLED


def test_sse_stream_end_to_end(tmp_path: Path) -> None:
    """Smoke (8.2): POST /query -> worker -> SSE status/token/done."""
    client, queue, store = make_app(tmp_path)
    task_id = client.post(
        "/query", headers=headers(), json={"query": "Что такое граф знаний?", "metadata": {"domain": "it"}}
    ).json()["task_id"]

    worker = make_worker(queue, store)
    assert worker.process_one() is True
    assert store.get(task_id)["status"] == "succeeded"

    resp = client.get(f"/query/tasks/{task_id}/stream", headers=headers())
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events: list[tuple[str, str]] = []
    for line in resp.iter_lines():
        if line.startswith("event: "):
            events.append((line[len("event: "):], ""))
        elif line.startswith("data: "):
            events[-1] = (events[-1][0], line[len("data: "):])

    types = [t for t, _ in events]
    assert "status" in types and "token" in types and "done" in types
    done = next(d for t, d in events if t == "done")
    assert "Граф знаний" in done


def test_worker_failure_publishes_error_and_fails_store(tmp_path: Path) -> None:
    from graphrag_proto.retrieval.adapters.base import Embedder

    class BoomEmbedder(Embedder):
        def embed(self, text: str, domain: str = "") -> list[float]:
            raise RuntimeError("эмбеддер упал")

    queue = InMemoryTaskQueue()
    store = TaskStore(tmp_path / "q2.sqlite")
    store.create("q_bad", "it", "v")
    queue.submit(Task(task_id="q_bad", domain="it", query="v"))
    worker = QueryWorker(
        queue=queue,
        store=store,
        pipeline=QueryPipeline(
            embedder=BoomEmbedder(),
            graph_store=InMemoryGraphStore(),
            vector_store=InMemoryVectorStore(),
            reranker=NoOpRerankerAdapter(),
            llm=FakeLLM(),
            profile_loader=DomainProfileLoader(),
        ),
    )
    assert worker.process_one() is True
    assert store.get("q_bad")["status"] == "failed"
    events = [(e.type, e.payload) for e in queue.events("q_bad")]
    error_events = [p for t, p in events if t == "error"]
    assert error_events, "воркер должен опубликовать error-событие"
    assert error_events[0]["message"] == "эмбеддер упал"


def test_worker_skips_reclaimed_task_already_terminal(tmp_path: Path) -> None:
    """S2 fencing: W1 завис после commit, задача reclaim'нута и заявлена W2 —
    возрождённая W2 видит терминальный статус и НЕ гоняет пайплайн повторно.
    """
    queue = InMemoryTaskQueue()
    store = TaskStore(tmp_path / "fence.sqlite")
    worker = make_worker(queue, store)

    store.create("q_fence", "it", "вопрос")
    queue.submit(Task(task_id="q_fence", domain="it", query="вопрос"))
    assert queue.claim("w1") is not None
    store.mark_succeeded("q_fence")  # W1 завершил commit (ack ещё не получен)
    queue.reclaim("w2", min_idle_s=0.0)  # появился в очереди заново

    assert worker.process_one() is True  # W2 claim -> терминальный статус -> skip + ack
    assert store.get("q_fence")["status"] == "succeeded"
    assert queue.depth() == 0
    # Канал не создан — пайплайн не запускался → ни status/token/done событий
    assert "q_fence" not in queue._channels


def test_inmemory_stale_ack_after_reclaim_ignored() -> None:
    """S2 fencing: stale ack от не-владельца — no-op (задача остаётся за владельцем)."""
    queue = InMemoryTaskQueue()
    queue.submit(Task(task_id="q_fence", domain="it", query="a"))
    queue.claim("w1")
    queue.reclaim("w2", min_idle_s=0.0)
    queue.claim("w2")

    queue.ack("q_fence", worker_id="w1")  # stale
    assert queue.depth() == 1
    queue.fail("q_fence", "err", worker_id="w1")  # stale
    assert queue.depth() == 1

    queue.ack("q_fence", worker_id="w2")  # владелец
    assert queue.depth() == 0


def _projection_store(
    domain: str,
    data_revision: str,
    *,
    status: str = "ready",
    config_fingerprint: str = "default",
) -> InMemoryProjectionStateStore:
    """Настоящий стор с опубликованной строкой: ревизию читают оттуда же, где она живёт."""
    store = InMemoryProjectionStateStore()
    store.put(
        ProjectionState(
            domain=domain,
            data_revision=data_revision,
            projection_revision=f"projection-for-{data_revision}",
            config_fingerprint=config_fingerprint,
            status=status,
        )
    )
    return store


class _FailingProjectionStore(InMemoryProjectionStateStore):
    """Стор, чтение которого падает.

    Дубль именно такой формы, потому что воспроизвести надо не поведение sqlite, а **отказ**
    чтения (закрытая или повреждённая база), и проверить границу гарантии: задача не падает,
    деградация видна.
    """

    def get(self, domain: str) -> ProjectionState | None:
        raise sqlite3.OperationalError("database is locked")


def test_worker_survives_projection_read_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Сбой чтения состояния проекции не роняет задачу и не молчит (ADR-026, поправка).

    Проверяется граница гарантии, а не логика стора: если бы `_data_revision` перестал
    ловить исключение, задача падала бы в `pipeline_error`, и заметить это можно было бы
    только на стенде с недоступной базой проекции. Сбой обязан быть виден в трёх местах —
    лог, счётчик и ответ (`revision_source = unavailable`).
    """
    queue = InMemoryTaskQueue()
    store = TaskStore(tmp_path / "unreachable.sqlite")
    seen: dict[str, object] = {}

    class CapturingPipeline:
        def active_domain(self) -> str:
            return "it"

        def run(
            self,
            query: str,
            domain: str | None,
            emit,
            revision: str | None = None,
            revision_source: str | None = None,
            max_depth: int | None = None,
            max_graph_nodes: int | None = None,
        ):
            seen["revision"] = revision
            seen["revision_source"] = revision_source
            return {"text": "ok", "revision": revision}

    store.create("q_down", "it", "вопрос")
    queue.submit(Task(task_id="q_down", domain="it", query="вопрос"))
    worker = QueryWorker(
        queue=queue,
        store=store,
        pipeline=CapturingPipeline(),  # type: ignore[arg-type]
        projections=_FailingProjectionStore(),
    )
    with caplog.at_level("WARNING", logger="graphrag_proto.query_service.worker"):
        assert worker.process_one() is True
    assert store.get("q_down")["status"] == "succeeded"
    assert seen == {"revision": None, "revision_source": "unavailable"}
    # Сбой не молчит: счётчик чтения проекции обязан вырасти, иначе деградация невидима.
    assert worker._projection_read_errors_total == 1
    assert any(
        "projection state read failed" in record.getMessage() for record in caplog.records
    )


def test_worker_passes_projection_revision_to_pipeline(tmp_path: Path) -> None:
    """Воркер читает `data_revision` из проекции и передаёт её в run() с источником."""
    queue = InMemoryTaskQueue()
    store = TaskStore(tmp_path / "rev.sqlite")
    seen: dict[str, object] = {}

    class CapturingPipeline:
        def active_domain(self) -> str:
            return "it"

        def run(
            self,
            query: str,
            domain: str | None,
            emit,
            revision: str | None = None,
            revision_source: str | None = None,
            max_depth: int | None = None,
            max_graph_nodes: int | None = None,
        ):
            seen["revision"] = revision
            seen["revision_source"] = revision_source
            seen["max_depth"] = max_depth
            return {"text": "ok", "revision": revision}

    store.create("q_rev", "it", "вопрос")
    queue.submit(Task(task_id="q_rev", domain="it", query="вопрос"))
    worker = QueryWorker(
        queue=queue,
        store=store,
        pipeline=CapturingPipeline(),  # type: ignore[arg-type]
        projections=_projection_store("it", "data-rev-A"),
    )
    assert worker.process_one() is True
    assert store.get("q_rev")["status"] == "succeeded"
    # `max_depth: None` - тоже утверждение: запрос не задавал глубину, значит воркер обязан
    # передать None, и решает цепочка конфигурации (ADR-025).
    assert seen == {
        "revision": "data-rev-A",
        "revision_source": "reported",
        "max_depth": None,
    }


def test_worker_reports_revision_unavailable_without_inventing_a_value(tmp_path: Path) -> None:
    """Строки проекции нет — `null` и `unavailable`, а не `""`, `0` или константа.

    Отсутствие данных и нулевое значение — разные результаты: подставленный `""` или `0`
    читался бы как измеренная ревизия и склеился бы с реальной эпохой кэша.
    """
    queue = InMemoryTaskQueue()
    store = TaskStore(tmp_path / "no-rev.sqlite")
    seen: dict[str, object] = {}

    class CapturingPipeline:
        def active_domain(self) -> str:
            return "it"

        def run(
            self,
            query: str,
            domain: str | None,
            emit,
            revision: str | None = None,
            revision_source: str | None = None,
            max_depth: int | None = None,
            max_graph_nodes: int | None = None,
        ):
            seen["revision"] = revision
            seen["revision_source"] = revision_source
            return {"text": "ok", "revision": revision}

    store.create("q_none", "it", "вопрос")
    queue.submit(Task(task_id="q_none", domain="it", query="вопрос"))
    worker = QueryWorker(
        queue=queue,
        store=store,
        pipeline=CapturingPipeline(),  # type: ignore[arg-type]
        projections=InMemoryProjectionStateStore(),  # домен ничего не публиковал
    )
    assert worker.process_one() is True
    assert store.get("q_none")["status"] == "succeeded"
    assert seen["revision"] is None
    assert seen["revision_source"] == "unavailable"
    assert seen["revision"] != "" and seen["revision"] != 0
    # Отсутствие строки — не ошибка чтения: счётчик обязан остаться нулём, иначе «не публиковали»
    # выглядело бы как сломанная база.
    assert worker._projection_read_errors_total == 0


@pytest.mark.parametrize(
    ("status", "data_revision"),
    [
        ("pending", "data-rev-not-published"),
        ("stale", "data-rev-not-published"),
        ("degraded", "data-rev-not-published"),
        ("failed", "data-rev-not-published"),
        ("ready", ""),
    ],
)
def test_worker_does_not_take_unpublished_or_empty_revision(
    tmp_path: Path, status: str, data_revision: str
) -> None:
    """Строка без опубликованной ревизии не даёт значения — даже `ready` с пустой строкой.

    `claim()` пишет `data_revision` до публикации, а снятие документа — со статусом
    `stale`. Если читать ревизию у любой строки, epoch кэша обгонит видимый индекс:
    записи, положенные до готовности, будут считаться записями новой ревизии. Пустая
    строка — тот же случай: `""` не должно стать известной ревизией и слипться с `default`.
    """
    queue = InMemoryTaskQueue()
    store = TaskStore(tmp_path / f"unpublished-{status}.sqlite")
    seen: dict[str, object] = {}

    class CapturingPipeline:
        def active_domain(self) -> str:
            return "it"

        def run(
            self,
            query: str,
            domain: str | None,
            emit,
            revision: str | None = None,
            revision_source: str | None = None,
            max_depth: int | None = None,
            max_graph_nodes: int | None = None,
        ):
            seen["revision"] = revision
            seen["revision_source"] = revision_source
            return {"text": "ok", "revision": revision}

    store.create("q_unpublished", "it", "вопрос")
    queue.submit(Task(task_id="q_unpublished", domain="it", query="вопрос"))
    worker = QueryWorker(
        queue=queue,
        store=store,
        pipeline=CapturingPipeline(),  # type: ignore[arg-type]
        projections=_projection_store("it", data_revision, status=status),
    )
    assert worker.process_one() is True
    assert seen["revision"] is None
    assert seen["revision_source"] == "unavailable"
    assert worker._projection_read_errors_total == 0


def test_worker_resolves_active_domain_before_revision_lookup(tmp_path: Path) -> None:
    queue = InMemoryTaskQueue()
    store = TaskStore(tmp_path / "active-rev.sqlite")
    seen: dict[str, object] = {}

    class CapturingPipeline:
        def active_domain(self) -> str:
            return "it"

        def run(
            self,
            query: str,
            domain: str | None,
            emit,
            revision: str | None = None,
            revision_source: str | None = None,
            max_depth: int | None = None,
            max_graph_nodes: int | None = None,
        ):
            seen["domain"] = domain
            seen["revision"] = revision
            seen["revision_source"] = revision_source
            seen["max_depth"] = max_depth
            seen["max_graph_nodes"] = max_graph_nodes
            return {"text": "ok", "revision": revision}

    store.create("q_active", "", "вопрос")
    queue.submit(Task(task_id="q_active", domain="", query="вопрос"))
    worker = QueryWorker(
        queue=queue,
        store=store,
        pipeline=CapturingPipeline(),
        projections=_projection_store("it", "data-rev-A"),
    )
    assert worker.process_one() is True
    assert seen == {
        "domain": "it",
        "revision": "data-rev-A",
        "revision_source": "reported",
        "max_depth": None,
        "max_graph_nodes": None,
    }


def test_worker_does_not_ask_ingestion_for_revision() -> None:
    """Воркер не делает HTTP-запросов к ингесту за ревизией (ADR-026, поправка).

    Гард по исходнику модуля: снятый клиент не должен вернуться ни импортом, ни вызовом —
    `requests` в воркере не остаётся ни для чего, а имя `RevisionClient` там больше не живёт.
    """
    import inspect

    from graphrag_proto.query_service import worker as worker_module

    source = inspect.getsource(worker_module)
    assert "RevisionClient" not in source
    assert "revision_client" not in source
    assert "revision_poll_errors_total" not in source
    assert "requests" not in source


def test_worker_metrics_snapshot_reports_projection_read_errors(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    queue = InMemoryTaskQueue()
    store = TaskStore(tmp_path / "m.sqlite")
    worker = QueryWorker(
        queue=queue,
        store=store,
        pipeline=make_worker(queue, store)._pipeline,
        projections=_projection_store("it", "data-rev-A"),
    )
    with caplog.at_level("INFO"):
        worker._log_metrics_snapshot(topology_poll_errors_total=2)
    snapshot = caplog.messages[-1]
    assert '"topology_poll_errors_total": 2' in snapshot
    assert '"projection_read_errors_total": 0' in snapshot
    assert "revision_poll_errors_total" not in snapshot
    assert '"revisions"' not in snapshot


def test_worker_metrics_snapshot_says_unknown_without_projection_store(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Стор не собран — измерять нечем, и в снапшоте стоит `null`, а не `0`."""
    queue = InMemoryTaskQueue()
    store = TaskStore(tmp_path / "m2.sqlite")
    worker = QueryWorker(
        queue=queue,
        store=store,
        pipeline=make_worker(queue, store)._pipeline,
    )
    with caplog.at_level("INFO"):
        worker._log_metrics_snapshot(topology_poll_errors_total=0)
    snapshot = caplog.messages[-1]
    assert '"projection_read_errors_total": null' in snapshot


def test_submit_accepts_max_depth_and_rides_to_the_task(tmp_path: Path) -> None:
    """Глубина из тела запроса доезжает до задачи: иначе воркеру нечего применять."""
    client, queue, _ = make_app(tmp_path)

    resp = client.post("/query", headers=headers(), json={"query": "вопрос", "max_depth": 4})

    assert resp.status_code == 202, resp.text
    task = queue.claim(worker_id="test", timeout_s=0.1)
    assert task is not None
    assert task.metadata["max_depth"] == 4


def test_submit_without_max_depth_leaves_metadata_clean(tmp_path: Path) -> None:
    """Отсутствие параметра не должно выглядеть как «глубина запрошена = None»."""
    client, queue, _ = make_app(tmp_path)

    client.post("/query", headers=headers(), json={"query": "вопрос"})

    task = queue.claim(worker_id="test", timeout_s=0.1)
    assert task is not None
    assert "max_depth" not in task.metadata


def test_submit_refuses_malformed_max_depth(tmp_path: Path) -> None:
    """Негодное значение отвергается на входе, а не внутри задачи.

    Отказ здесь, а не в воркере, потому что он ничего не стоит: не создаётся задача,
    не занимается очередь и не тратится обращение к модели. Зажим сверху (99) при этом
    НЕ отвергается - это объявление о намерении, см. ADR-036.
    """
    client, queue, _ = make_app(tmp_path)

    for bad in (0, -1, 2.5, "3", True):
        resp = client.post("/query", headers=headers(), json={"query": "вопрос", "max_depth": bad})
        assert resp.status_code == 422, f"{bad!r} -> {resp.status_code}"

    assert queue.claim(worker_id="test", timeout_s=0.1) is None, (
        "негодный запрос не должен создавать задачу"
    )


def test_submit_accepts_depth_above_cap_without_refusing(tmp_path: Path) -> None:
    """Просьба глубже потолка принимается и уйдёт в конвейер, где будет зажата."""
    client, queue, _ = make_app(tmp_path)

    resp = client.post("/query", headers=headers(), json={"query": "вопрос", "max_depth": 99})

    assert resp.status_code == 202, resp.text
    task = queue.claim(worker_id="test", timeout_s=0.1)
    assert task is not None
    assert task.metadata["max_depth"] == 99


def test_submit_accepts_max_graph_nodes_and_rides_to_the_task(tmp_path: Path) -> None:
    """Лимит узлов обхода из тела запроса доезжает до задачи (ADR-069).

    Без этого человек двигает ползунок глубины и не может понять, почему при лимите 5
    увеличение глубины ничего не меняет: лимит не настраивался ниоткуда, кроме профиля.
    """
    client, queue, _ = make_app(tmp_path)

    resp = client.post(
        "/query", headers=headers(), json={"query": "вопрос", "max_graph_nodes": 40}
    )

    assert resp.status_code == 202, resp.text
    task = queue.claim(worker_id="test", timeout_s=0.1)
    assert task is not None
    assert task.metadata["max_graph_nodes"] == 40


def test_submit_refuses_malformed_max_graph_nodes(tmp_path: Path) -> None:
    """Негодный лимит отвергается на входе, не создавая задачу — как и для глубины."""
    client, queue, _ = make_app(tmp_path)

    for bad in (0, -1, 2.5, "7", True):
        resp = client.post(
            "/query", headers=headers(), json={"query": "вопрос", "max_graph_nodes": bad}
        )
        assert resp.status_code == 422, f"{bad!r} -> {resp.status_code}"

    assert queue.claim(worker_id="test", timeout_s=0.1) is None


def test_submit_without_max_graph_nodes_leaves_metadata_clean(tmp_path: Path) -> None:
    """Нет параметра - нет и записи: профиль остаётся единственным источником умолчания."""
    client, queue, _ = make_app(tmp_path)

    client.post("/query", headers=headers(), json={"query": "вопрос"})

    task = queue.claim(worker_id="test", timeout_s=0.1)
    assert task is not None
    assert "max_graph_nodes" not in task.metadata
