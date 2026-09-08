"""Сборка зависимостей Query Service из env (M2; runtime-конфиг переключения — M3)."""

from __future__ import annotations

import os
from pathlib import Path

from graphrag_proto.query_service.store import TaskStore
from graphrag_proto.query_service.task_queue import (
    InMemoryTaskQueue,
    RedisStreamTaskQueue,
    TaskQueue,
)
from graphrag_proto.retrieval.adapters.factory import (
    build_embedder,
    build_graph_store,
    build_llm,
    build_reranker,
    build_vector_store,
)
from graphrag_proto.retrieval.pipeline import QueryPipeline
from graphrag_proto.retrieval.profile import DomainProfileLoader


def build_store(db_path: str | None = None) -> TaskStore:
    return TaskStore(Path(db_path) if db_path else Path(os.environ.get("QUERY_TASK_DB_PATH", "runtime/query_tasks.sqlite")))


def build_queue() -> TaskQueue:
    kind = os.environ.get("QUERY_QUEUE", "inmemory").strip().lower()
    if kind == "redis":
        return RedisStreamTaskQueue(redis_url=os.environ.get("QUERY_REDIS_URL", "redis://valkey:6379/0"))
    if kind in ("inmemory", ""):
        return InMemoryTaskQueue()
    raise ValueError(f"QUERY_QUEUE={kind!r}: допустимо redis|inmemory")


def build_pipeline() -> QueryPipeline:
    return QueryPipeline(
        embedder=build_embedder(),
        graph_store=build_graph_store(),
        vector_store=build_vector_store(),
        reranker=build_reranker(),
        llm=build_llm(),
        profile_loader=DomainProfileLoader(config_url=os.environ.get("CONFIG_URL", "")),
    )


def build_api_key() -> str:
    return os.environ.get("AUTH_API_KEY", "changeme")