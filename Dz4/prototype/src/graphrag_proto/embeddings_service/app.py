"""Embeddings Service API (:8004) — add-real-embeddings-reranker, ADR-012.

REST-контур:
- `GET  /health`                 — статус, модель, размерность, режим;
- `POST /api/v1/embed`           — вектор текста `{text, domain?}`;
- `POST /api/v1/embed/batch`     — пачка векторов `{texts[], domain?}`.

Контракт и данные: specs/add-real-embeddings-reranker. Пустой текст -> 422;
недоступная модель/инференс -> 503 (без тихой деградации размерности оси L2-04).
Все эндпоинты кроме /health требуют `X-API-Key` (L5-01).
Провайдер и ключ инъецируются для тестов (`create_app(provider=..., api_key=...)`).
"""

from __future__ import annotations

import os
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException
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
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def create_app(provider: EmbeddingProvider | None = None, api_key: str = "changeme") -> FastAPI:
    provider = provider or provider_from_env()
    app = FastAPI(title="GraphRAG Embeddings Service", version="0.1.0")

    def require_key(x_api_key: str | None = Header(None, alias="X-API-Key")) -> None:
        if x_api_key != api_key:
            raise HTTPException(status_code=401, detail="неверный или отсутствующий X-API-Key")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "embeddings",
            "model": provider.model,
            "dimensions": provider.dimensions,
            "mode": provider.mode,
        }

    @app.post("/api/v1/embed", dependencies=[Depends(require_key)])
    def embed(req: EmbedRequest) -> dict[str, Any]:
        return {"vector": _run_embed(provider, req.text), "dimensions": provider.dimensions}

    @app.post("/api/v1/embed/batch", dependencies=[Depends(require_key)])
    def embed_batch(req: BatchEmbedRequest) -> dict[str, Any]:
        vectors = [_run_embed(provider, text) for text in req.texts]
        return {"vectors": vectors, "dimensions": provider.dimensions}

    return app


def main() -> None:
    from graphrag_proto.security import install_redaction

    install_redaction()
    api_key = os.environ.get("AUTH_API_KEY") or os.environ.get("GRAPH_AUTH_API_KEY", "changeme")
    uvicorn.run(create_app(api_key=api_key), host=HOST, port=PORT)