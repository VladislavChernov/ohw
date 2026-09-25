"""Сборка зависимостей Query Service из env (M2; переключение на лету — M3)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from graphrag_proto.ingestion_service.projection import (
    ProjectionStateStore,
    try_build_projection_state_store,
)
from graphrag_proto.query_service.store import TaskStore
from graphrag_proto.query_service.task_queue import (
    InMemoryTaskQueue,
    RedisStreamTaskQueue,
    TaskQueue,
)
from graphrag_proto.redis_config import load_redis_settings
from graphrag_proto.retrieval.adapters.factory import build_adapters
from graphrag_proto.retrieval.pipeline import QueryPipeline
from graphrag_proto.retrieval.profile import DomainProfileLoader
from graphrag_proto.retrieval.semantic_cache import (
    InMemorySemanticCache,
    RedisSemanticCache,
    SemanticCache,
)


def build_store(db_path: str | None = None) -> TaskStore:
    return TaskStore(Path(db_path) if db_path else Path(os.environ.get("QUERY_TASK_DB_PATH", "runtime/query_tasks.sqlite")))


def build_queue() -> TaskQueue:
    kind = os.environ.get("QUERY_QUEUE", "inmemory").strip().lower()
    if kind == "redis":
        return RedisStreamTaskQueue(settings=load_redis_settings())
    if kind in ("inmemory", ""):
        return InMemoryTaskQueue()
    raise ValueError(f"QUERY_QUEUE={kind!r}: допустимо redis|inmemory")


def _cache_threshold() -> float:
    try:
        threshold = float(os.environ.get("SEMANTIC_CACHE_THRESHOLD", "0.85"))
    except ValueError:
        raise ValueError("SEMANTIC_CACHE_THRESHOLD должен быть числом") from None
    if not 0.0 < threshold <= 1.0:
        raise ValueError(f"SEMANTIC_CACHE_THRESHOLD={threshold:g} вне диапазона (0, 1]")
    return threshold


def _cache_ttl() -> float:
    raw = os.environ.get("SEMANTIC_CACHE_TTL_S", "3600").strip()
    try:
        ttl = float(raw)
    except ValueError:
        return 0.0  # нечисловое -> без истечения (spec)
    return ttl if ttl > 0 else 0.0


def build_semantic_cache() -> SemanticCache | None:
    """Сборка кэша из env (бандл 3/3). None: SEMANTIC_CACHE_ENABLED пусто/false — выключено.

    `SEMANTIC_CACHE_MODE` = inmemory | redis; порог (0, 1], TTL (0 — без истечения).
    Объект создаётся в worker.main() один раз и переживает hot-reload пересборку.
    """
    enabled = os.environ.get("SEMANTIC_CACHE_ENABLED", "").strip()
    if not enabled or enabled.lower() in ("0", "false", "no", "off"):
        return None
    threshold = _cache_threshold()
    ttl_s = _cache_ttl()
    mode = os.environ.get("SEMANTIC_CACHE_MODE", "inmemory").strip().lower()
    if mode == "inmemory":
        return InMemorySemanticCache(threshold=threshold, ttl_s=ttl_s)
    if mode == "redis":
        return RedisSemanticCache(
            settings=load_redis_settings(),
            threshold=threshold,
            ttl_s=ttl_s,
        )
    raise ValueError(f"SEMANTIC_CACHE_MODE={mode!r}: допустимо inmemory|redis")


def build_pipeline(
    adapter_map: Mapping[str, str] | None = None,
    semantic_cache: SemanticCache | None = None,
    strict_profile: bool = False,
    projection_state_store: ProjectionStateStore | None = None,
) -> QueryPipeline:
    """Сборка пайплайна: карта топологии (M3) > env/дефолт (M2); кэш передаётся явно."""
    adapters = build_adapters(adapter_map)
    state_store = projection_state_store or try_build_projection_state_store()
    config_fingerprint = os.environ.get("PROJECTION_CONFIG_FINGERPRINT", "default")
    return QueryPipeline(
        embedder=adapters.embedder,
        graph_store=adapters.graph_store,
        vector_store=adapters.vector_store,
        reranker=adapters.reranker,
        llm=adapters.llm,
        profile_loader=DomainProfileLoader(
            config_url=os.environ.get("CONFIG_URL", ""),
            strict=strict_profile,
        ),
        semantic_cache=semantic_cache,
        strict_profile=strict_profile,
        projection_state_store=state_store,
        projection_config_fingerprint=config_fingerprint,
        projection_state_required=True,
    )


def build_api_key() -> str:
    return os.environ.get("AUTH_API_KEY", "changeme")