"""Сборка зависимостей Query Service из env (M2; переключение на лету — M3)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from graphrag_proto.query_service.store import TaskStore
from graphrag_proto.query_service.task_queue import (
    InMemoryTaskQueue,
    RedisStreamTaskQueue,
    TaskQueue,
)
from graphrag_proto.retrieval.adapters.factory import build_adapters
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


def build_pipeline(adapter_map: Mapping[str, str] | None = None) -> QueryPipeline:
    """Сборка пайплайна: карта топологии (M3) > env/дефолт (M2)."""
    adapters = build_adapters(adapter_map)
    return QueryPipeline(
        embedder=adapters.embedder,
        graph_store=adapters.graph_store,
        vector_store=adapters.vector_store,
        reranker=adapters.reranker,
        llm=adapters.llm,
        profile_loader=DomainProfileLoader(config_url=os.environ.get("CONFIG_URL", "")),
    )


def build_api_key() -> str:
    return os.environ.get("AUTH_API_KEY", "changeme")