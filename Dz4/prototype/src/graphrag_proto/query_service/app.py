"""Query API (:8000) — асинхронный контур (docs/api_reference.md §3, ADR-016/023).

POST /query -> 202 {task_id, status, accepted_at}
GET  /query/tasks/{task_id}      — статус (lifecycle queued->running->succeeded|failed|cancelled)
GET  /query/tasks/{task_id}/stream — SSE (event: status/token/done/error, конверт ADR-016)
DELETE /query/tasks/{task_id}    — отмена
GET  /health
Все запросы кроме /health требуют X-API-Key (L5-01).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import Body, Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from graphrag_proto.query_service.models import Task, new_task_id
from graphrag_proto.query_service.store import STATUS_CANCELLED, STATUS_QUEUED, TERMINAL, TaskStore
from graphrag_proto.query_service.task_queue import TaskQueue

HOST = "0.0.0.0"
PORT = 8000


def create_app(
    queue: TaskQueue,
    store: TaskStore,
    api_key: str = "changeme",
) -> FastAPI:
    app = FastAPI(title="GraphRAG Query API", version="0.1.0")

    def require_key(x_api_key: str | None = Header(None, alias="X-API-Key")) -> None:
        if x_api_key != api_key:
            raise HTTPException(status_code=401, detail="неверный или отсутствующий X-API-Key")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/query", dependencies=[Depends(require_key)])
    def submit_query(payload: dict[str, Any] = Body(...)) -> JSONResponse:  # noqa: B008
        query = payload.get("query")
        if not isinstance(query, str) or not query.strip():
            raise HTTPException(status_code=422, detail="поле 'query' обязано быть непустой строкой")
        metadata = payload.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        domain = metadata.get("domain")
        if domain is not None and not isinstance(domain, str):
            raise HTTPException(status_code=422, detail="metadata.domain должен быть строкой")

        task_id = new_task_id()
        store.create(task_id, domain or "", query)
        try:
            queue.submit(Task(task_id=task_id, domain=domain or "", query=query, metadata=metadata))
        except Exception as exc:
            store.mark_failed(task_id, str(exc))
            raise HTTPException(status_code=503, detail=f"очередь недоступна: {exc}") from exc
        return JSONResponse(
            {
                "task_id": task_id,
                "status": STATUS_QUEUED,
                "accepted_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            },
            status_code=202,
        )

    @app.get("/query/tasks/{task_id}", dependencies=[Depends(require_key)])
    def get_task(task_id: str) -> dict[str, Any]:
        task = store.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"задача {task_id} не найдена")
        return {"task_id": task["task_id"], "status": task["status"], "stage": task["stage"]}

    @app.delete("/query/tasks/{task_id}", dependencies=[Depends(require_key)])
    def cancel_task(task_id: str) -> JSONResponse:
        task = store.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"задача {task_id} не найдена")
        if task["status"] in TERMINAL:
            raise HTTPException(status_code=409, detail=f"задача уже в статусе {task['status']}")
        try:
            queue.cancel(task_id)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"очередь недоступна: {exc}") from exc
        if not store.mark_cancelled(task_id):
            raise HTTPException(status_code=409, detail="задача уже завершилась")
        return JSONResponse({"task_id": task_id, "status": STATUS_CANCELLED})

    @app.get("/query/tasks/{task_id}/stream", dependencies=[Depends(require_key)])
    def stream_task(task_id: str) -> StreamingResponse:
        if store.get(task_id) is None:
            raise HTTPException(status_code=404, detail=f"задача {task_id} не найдена")

        def generate() -> Any:
            for event in queue.events(task_id):
                body = {
                    "type": event.type,
                    "task_id": event.task_id,
                    "ts": event.ts,
                    "payload": event.payload,
                }
                yield f"event: {event.type}\ndata: {json.dumps(body, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


def main() -> None:
    import uvicorn

    from graphrag_proto.query_service.runtime import build_api_key, build_queue, build_store

    uvicorn.run(
        create_app(queue=build_queue(), store=build_store(), api_key=build_api_key()),
        host=HOST,
        port=PORT,
    )


if __name__ == "__main__":
    main()