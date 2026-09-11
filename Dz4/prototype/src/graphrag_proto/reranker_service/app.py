"""Reranker Service API (:8006) — add-real-embeddings-reranker, ADR-012.

REST-контур:
- `GET  /health`             — статус, модель, режим;
- `POST /api/v1/rerank`      — скоры `{query, chunks[]}` (выравнивание по индексу).

Контракт и данные: specs/add-real-embeddings-reranker. Пустой query/chunks -> 422;
недоступная модель/инференс -> 503. Скорер инъецируется для тестов
(`create_app(scorer=...)`).
"""

from __future__ import annotations

from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
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
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def create_app(scorer: RerankScorer | None = None) -> FastAPI:
    scorer = scorer or scorer_from_env()
    app = FastAPI(title="GraphRAG Reranker Service", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "service": "reranker", "model": scorer.model, "mode": scorer.mode}

    @app.post("/api/v1/rerank")
    def rerank(req: RerankRequest) -> dict[str, Any]:
        texts = [chunk.text for chunk in req.chunks]
        scores = _run_score(scorer, req.query, texts)
        return {"scores": scores}

    return app


def main() -> None:
    uvicorn.run(create_app(), host=HOST, port=PORT)