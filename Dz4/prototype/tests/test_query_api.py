"""Query API (:8000): X-API-Key, 202/409/404, SSE stream через worker (8.2 smoke)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

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