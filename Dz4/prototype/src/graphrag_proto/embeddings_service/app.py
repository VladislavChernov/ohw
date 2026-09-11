"""Embeddings Service API (:8004) — add-real-embeddings-reranker, ADR-012.

REST-контур:
- `GET  /health`                 — статус, модель, размерность, режим;
- `POST /api/v1/embed`           — вектор текста `{text, domain?}`;
- `POST /api/v1/embed/batch`     — пачка векторов `{texts[], domain?}`.

Контракт и данные: specs/add-real-embeddings-reranker. Пустой текст -> 422;
недоступная модель/инференс -> 503 (без тихой деградации размерности оси L2-04).
Провайдер инъецируется для тестов (`create_app(provider=...)`).
"""

from __future__ import annotations

from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from graphrag_proto.embeddings_service.model import EmbeddingProvider, provider_from_env

HOST = "0.0.0.0"
PORT = 8004


class EmbedRequest(BaseModel):
    text: str = Field(min_length=1)
    domain: str = ""


class BatchEmbedRequest(BaseModel):
    texts: list[str] = Field(min_length=1)
    domain: str = ""


def _run_embed(provider: EmbeddingProvider, text: str) -> list[float]:
    if not text.strip():
        raise HTTPException(status_code=422, detail="пустой текст")
    try:
        return provider.embed(text)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def create_app(provider: EmbeddingProvider | None = None) -> FastAPI:
    provider = provider or provider_from_env()
    app = FastAPI(title="GraphRAG Embeddings Service", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "embeddings",
            "model": provider.model,
            "dimensions": provider.dimensions,
            "mode": provider.mode,
        }

    @app.post("/api/v1/embed")
    def embed(req: EmbedRequest) -> dict[str, Any]:
        return {"vector": _run_embed(provider, req.text), "dimensions": provider.dimensions}

    @app.post("/api/v1/embed/batch")
    def embed_batch(req: BatchEmbedRequest) -> dict[str, Any]:
        vectors = [_run_embed(provider, text) for text in req.texts]
        return {"vectors": vectors, "dimensions": provider.dimensions}

    return app


def main() -> None:
    uvicorn.run(create_app(), host=HOST, port=PORT)