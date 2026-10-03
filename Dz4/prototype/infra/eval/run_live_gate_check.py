"""Гоняет боевой гейт проекции против живого Neo4j и живой SQLite (ADR-046 п. 3).

Собирает ровно те зависимости, которые получает `QueryPipeline` в сервисе, и печатает
решение гейта вместе со счётчиками. Никаких заглушек: цель — проверить, что ось графа
на живых данных действительно открывается, а не только то, что счётчики сходятся.
"""

from __future__ import annotations

import json
import os
import pathlib

from graphrag_proto.ingestion_service.projection import SQLiteProjectionStateStore
from graphrag_proto.retrieval.adapters.base import Embedder, LLMInference, Reranker
from graphrag_proto.retrieval.adapters.deterministic import deterministic_embedding
from graphrag_proto.retrieval.adapters.neo4j import Neo4jGraphStore, Neo4jVectorStore
from graphrag_proto.retrieval.pipeline import QueryPipeline

DOMAIN = os.environ.get("DOMAIN", "it")


class _Embedder(Embedder):
    def embed(self, text: str, domain: str | None = None) -> list[float]:
        return deterministic_embedding(text)

    def embed_batch(self, texts: list[str], domain: str | None = None) -> list[list[float]]:
        return [deterministic_embedding(text) for text in texts]


class _Reranker(Reranker):
    def rerank(self, query: str, hits: list[dict], top_k: int | None = None) -> list[dict]:
        return hits if top_k is None else hits[:top_k]


class _LLM(LLMInference):
    def generate(self, prompt: str, *, max_tokens: int = 64) -> str:
        return "ok"


def main() -> int:
    graph = Neo4jGraphStore(
        uri=os.environ["NEO4J_BOLT"],
        user=os.environ.get("NEO4J_USER", "neo4j"),
        password=os.environ["NEO4J_PASSWORD"],
    )
    vector = Neo4jVectorStore(
        uri=os.environ["NEO4J_BOLT"],
        user=os.environ.get("NEO4J_USER", "neo4j"),
        password=os.environ["NEO4J_PASSWORD"],
    )
    state = SQLiteProjectionStateStore(pathlib.Path(os.environ["PROJECTION_STATE_DB"]))
    pipeline = QueryPipeline(
        embedder=_Embedder(),
        graph_store=graph,
        vector_store=vector,
        reranker=_Reranker(),
        llm=_LLM(),
        projection_state_store=state,
        projection_config_fingerprint=os.environ.get("PROJECTION_CONFIG_FINGERPRINT", "default"),
        projection_state_required=True,
    )
    stored = state.get(DOMAIN)
    print(json.dumps({
        
        "state_status": None if stored is None else stored.status,
        "state_data_revision": None if stored is None else stored.data_revision[:20],
        "state_projection_revision": None if stored is None else stored.projection_revision[:20],
        "journal_sources": len(state.source_revisions(DOMAIN)),
    }, ensure_ascii=False, indent=2))

    answer = pipeline.run(
        "дедупликация кэширование",
        domain=DOMAIN,
        revision=stored.data_revision if stored else None,
        generate=False,
        trace=True,
    )
    events = answer.get("trace") or answer.get("events") or []
    readiness = [
        {"stage": event.get("stage"), "degraded": event.get("degraded"), "status": event.get("status"), "reason": event.get("reason")}
        for event in events
        if isinstance(event, dict)
    ]
    print(json.dumps({
        "graph_degraded": answer.get("graph_degraded"),
        "degraded_reasons": {k: v for k, v in answer.items() if "degrad" in k or "reason" in k},
        "projection_status": answer.get("projection_status"),
        "projection_revision": (answer.get("projection_revision") or "")[:20],
        "graph_readiness_events": readiness,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())