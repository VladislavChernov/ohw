"""Reranker Service API (:8006) — add-real-embeddings-reranker, ADR-012.

REST-контур:
- `GET  /health`             — статус, модель, режим;
- `POST /api/v1/rerank`      — скоры `{query, chunks[]}` (выравнивание по индексу).

Контракт и данные: specs/add-real-embeddings-reranker. Пустой query/chunks -> 422;
недоступная модель/инференс -> 503. Все эндпоинты кроме /health требуют
`X-API-Key` (L5-01). Скорер и ключ инъецируются для тестов
(`create_app(scorer=..., api_key=...)`).
"""

from __future__ import annotations

import os
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from graphrag_proto.reranker_service.model import RerankScorer, scorer_from_env

HOST = "0.0.0.0"
PORT = 8006


class RerankChunk(BaseModel):
    id: str = ""
    text: str = Field(min_length=1)


class RerankRequest(BaseModel):
    query: str = Field(min_length=1)
    chunks: list[RerankChunk] = Field(min_length=1)


def _run_score(scorer: RerankScorer, query: str, texts: list[str]) -> list[float]:
    if not query.strip():
        raise HTTPException(status_code=422, detail="пустой query")
    if not texts or any(not t.strip() for t in texts):
        raise HTTPException(status_code=422, detail="пустые чанки")
    try:
        return scorer.score(query, texts)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def create_app(scorer: RerankScorer | None = None, api_key: str = "changeme") -> FastAPI:
    scorer = scorer or scorer_from_env()
    app = FastAPI(title="GraphRAG Reranker Service", version="0.1.0")

    def require_key(x_api_key: str | None = Header(None, alias="X-API-Key")) -> None:
        if x_api_key != api_key:
            raise HTTPException(status_code=401, detail="неверный или отсутствующий X-API-Key")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "service": "reranker", "model": scorer.model, "mode": scorer.mode}

    @app.post("/api/v1/rerank", dependencies=[Depends(require_key)])
    def rerank(req: RerankRequest) -> dict[str, Any]:
        texts = [chunk.text for chunk in req.chunks]
        scores = _run_score(scorer, req.query, texts)
        return {"scores": scores}

    return app


def main() -> None:
    from graphrag_proto.security import install_redaction

    install_redaction()
    api_key = os.environ.get("AUTH_API_KEY") or os.environ.get("GRAPH_AUTH_API_KEY", "changeme")
    uvicorn.run(create_app(api_key=api_key), host=HOST, port=PORT)