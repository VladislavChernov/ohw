"""Topology Orchestrator API (:8005) — add-topology-adapters, ADR-019.

REST-контур:
- `GET  /api/v1/topology`           — топология инсталляции (база из yaml);
- `GET  /api/v1/config/adapters`    — эффективная карта адаптеров + revision;
- `PUT  /api/v1/config/adapters`    — переключение на лету (валидация по каталогу);
- `GET  /api/v1/config/adapters/available` — реализованные провайдеры;
- `GET  /health`.
Все эндпоинты кроме /health требуют `X-API-Key` (единый AUTH_API_KEY стека).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, Header, HTTPException

from graphrag_proto.topology_service.catalog import SLOT_ORDER, available_providers, is_known
from graphrag_proto.topology_service.store import TopologyStore
from graphrag_proto.topology_service.topology import (
    TopologyError,
    base_providers,
    load_topology,
)

HOST = "0.0.0.0"
PORT = 8005


def create_app(
    topology_path: str | Path,
    db_path: str | Path,
    api_key: str = "changeme",
) -> FastAPI:
    topology = load_topology(topology_path)
    if isinstance(topology_path, Path):
        topology_path = str(topology_path)
    store = TopologyStore(Path(db_path))

    app = FastAPI(title="GraphRAG Topology Orchestrator", version="0.1.0")

    def require_key(x_api_key: str | None = Header(None, alias="X-API-Key")) -> None:
        if x_api_key != api_key:
            raise HTTPException(status_code=401, detail="неверный или отсутствующий X-API-Key")

    def effective_adapters() -> dict[str, str]:
        overrides = store.overrides()
        return {slot: overrides.get(slot, base_providers(topology)[slot]) for slot in SLOT_ORDER}

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/topology", dependencies=[Depends(require_key)])
    def topology_view() -> dict[str, Any]:
        return {
            "version": topology["version"],
            "environment": topology["environment"],
            "network": topology["network"],
            "providers": base_providers(topology),
            "endpoints": topology["endpoints"],
            "startup": topology["startup"],
            "revision": store.revision(),
        }

    @app.get("/api/v1/config/adapters", dependencies=[Depends(require_key)])
    def get_adapters() -> dict[str, Any]:
        return {"revision": store.revision(), "adapters": effective_adapters()}

    @app.put("/api/v1/config/adapters", dependencies=[Depends(require_key)])
    def put_adapters(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:  # noqa: B008
        updates: dict[str, str] = {}
        for slot, value in payload.items():
            if slot not in SLOT_ORDER:
                raise HTTPException(status_code=422, detail=f"неизвестный слот '{slot}'")
            if not isinstance(value, str):
                raise HTTPException(status_code=422, detail=f"provider слота '{slot}' должен быть строкой")
            provider = value.strip().lower()
            if not is_known(slot, provider):
                catalog = available_providers()
                raise HTTPException(
                    status_code=422,
                    detail=f"providers.{slot}: неизвестный провайдер '{value}', допустимо {catalog[slot]}",
                )
            updates[slot] = provider

        revision = store.apply_overrides(updates) if updates else store.revision()
        return {"revision": revision, "adapters": effective_adapters()}

    @app.get("/api/v1/config/adapters/available", dependencies=[Depends(require_key)])
    def get_available() -> dict[str, Any]:
        return {"slots": available_providers()}

    return app


def main() -> None:
    import uvicorn

    topology_path = os.environ.get("INFRA_TOPOLOGY_PATH", "infra_topology.yaml")
    db_path = os.environ.get("TOPOLOGY_DB_PATH", "runtime/topology.sqlite")
    api_key = os.environ.get("AUTH_API_KEY") or os.environ.get("GRAPH_AUTH_API_KEY", "changeme")
    try:
        app = create_app(topology_path, db_path, api_key)
    except TopologyError as exc:
        raise SystemExit(f"конфигурация топологии невалидна: {exc}") from exc
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()