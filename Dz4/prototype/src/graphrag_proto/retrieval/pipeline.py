"""QueryPipeline — 7 шагов retrieval-цикла (docs/03_retriever.md, D3 design M2):

1. embedding (DeterministicEmbedder, L4-01) → status: embedding
2. semantic cache (бандл 3/3, add-semantic-cache): hit → status: cache{hit:true} → done
   (без store/LLM, без token); miss → обычный цикл + store() перед done
3. baseline: vector search с metadata; optional graph experiment выполняет bounded expansion
   только при `graph_search_enabled=true` и готовой projection
4. rerank (NoOp) → status: rerank
5. Context Assembly (context.py, L3-03/L3-04)
6. LLM streaming (OpenAICompatibleAdapter / FakeLLM) → status: llm, token*
7. done — {text, sources, generation_time_s, retrieval_time_s, total_time_s, revision[, cache_hit, cache_lookup_s]}

emit(event_type, payload) — обратный вызов воркера (публикует события конверта ADR-016).
`revision` — fingerprint ревизии данных домена (ADR-026): в любом исходе (в т.ч.
cache-hit и при генерации) — «по каким данным собран ответ», срез для Eval (ADR-015).
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from graphrag_proto.ingestion_service.projection import (
    ProjectionMetrics,
    ProjectionState,
    ProjectionStateStore,
    projection_revision_for,
)
from graphrag_proto.retrieval.adapters.base import (
    Embedder,
    GraphStoreProvider,
    LLMInference,
    Reranker,
    VectorStoreProvider,
)
from graphrag_proto.retrieval.context import (
    CONTEXT_TOKEN_LIMIT,
    ContextAssembly,
    estimate_tokens,
    render_skeleton,
)
from graphrag_proto.retrieval.profile import DomainProfileLoader, ProfileError
from graphrag_proto.retrieval.retrievers import GraphRetriever, VectorRetriever
from graphrag_proto.retrieval.semantic_cache import CachedAnswer, SemanticCache

Emit = Callable[[str, dict[str, Any]], None]

DEFAULT_SYSTEM_PROMPT = (
    "Ты — GraphRAG-ассистент. Отвечай строго по предоставленному контексту. "
    "Ссылайся на источники из контекста. Если контекста недостаточно — так и скажи."
)


def graph_search_enabled(profile: dict[str, Any]) -> bool:
    """Тумблер optional graph experiment: env > profile > disabled by default.

    `RETRIEVAL_GRAPH_ENABLED=false` всегда отключает experiment; без явного true baseline
    остаётся vector-only.
    """
    env = os.environ.get("RETRIEVAL_GRAPH_ENABLED")
    if env is not None and env.strip():
        return env.strip().lower() == "true"
    retrieval = profile.get("retrieval") or {}
    return bool(retrieval.get("graph_search_enabled", False))


def _validate_runtime_profile(profile: dict[str, Any]) -> None:
    if not isinstance(profile, dict):
        raise ProfileError("Domain Profile должен быть mapping")


def _noop_emit(event_type: str, payload: dict[str, Any]) -> None:
    return None


def build_sources(
    body_chunks: list[dict[str, Any]],
    skeleton_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Уникальные источники с максимальным relevance по паре (source_url, axis).

    Ось `vector` — из body-чанков, ось `graph` — из узлов графового скелета
    (design.md §2, аддитивное поле `axis`, ADR-016). `retrieval_metrics`
    по-прежнему работает по `source_url`.
    """
    best: dict[tuple[str, str], float] = {}
    for chunk in body_chunks:
        source = str(chunk.get("source_url") or "")
        if not source:
            continue
        score = float(chunk.get("score") or 0.0)
        key = (source, "vector")
        best[key] = max(best.get(key, 0.0), score)
    for row in skeleton_rows or []:
        for url in _skeleton_source_urls(row):
            best[(url, "graph")] = max(best.get((url, "graph"), 0.0), 1.0)
    ordered = sorted(
        best.items(),
        key=lambda pair: (0 if pair[0][1] == "graph" else 1, -pair[1], pair[0][0]),
    )
    return [
        {"source_url": source, "relevance": round(score, 4), "axis": axis}
        for (source, axis), score in ordered
    ]


def _skeleton_source_urls(row: dict[str, Any]) -> list[str]:
    """source_url'ы узлов скелета: у Source/Chunk — `source_url`, у канонических узлов — `source_ids`."""
    urls: list[str] = []
    for node in (row.get("n") or {}, row.get("m") or {}):
        if not isinstance(node, dict):
            continue
        value = node.get("source_url") or node.get("source_ids")
        if isinstance(value, str):
            if value:
                urls.append(value)
        elif isinstance(value, list):
            urls.extend(str(u) for u in value if str(u))
    return urls


class QueryPipeline:
    def __init__(
        self,
        embedder: Embedder,
        graph_store: GraphStoreProvider | None,
        vector_store: VectorStoreProvider,
        reranker: Reranker,
        llm: LLMInference,
        profile_loader: DomainProfileLoader | None = None,
        max_graph_nodes: int = 5,
        max_vector_chunks: int = 5,
        executor: ThreadPoolExecutor | None = None,
        semantic_cache: SemanticCache | None = None,
        strict_profile: bool = False,
        projection_state_store: ProjectionStateStore | None = None,
        projection_config_fingerprint: str | None = None,
        projection_state_required: bool = False,
        projection_metrics: ProjectionMetrics | None = None,
    ) -> None:
        self._embedder = embedder
        self._graph_store = graph_store
        self._vector_store = vector_store
        self._reranker = reranker
        self._llm = llm
        self._profiles = profile_loader or DomainProfileLoader()
        self._strict_profile = strict_profile
        self._projection_state_store = projection_state_store
        self._projection_config_fingerprint = projection_config_fingerprint
        self._projection_state_required = projection_state_required
        self.projection_metrics = projection_metrics or ProjectionMetrics()
        self._max_graph_nodes = max_graph_nodes
        self._max_vector_chunks = max_vector_chunks
        self._executor = executor or ThreadPoolExecutor(max_workers=2, thread_name_prefix="query-pipeline")
        # Semantic Cache (бандл 3/3): None — выключено (поведение M2/M3, backward-compat).
        self._semantic_cache = semantic_cache
        self._cache_threshold = semantic_cache.threshold if semantic_cache is not None else 0.0
        self._closed = False

    def shutdown(self) -> None:
        """Закрытие общего executor'а (вызывается при замене пайплайна/остановке воркера)."""
        self._closed = True
        self._executor.shutdown(wait=False)

    def active_domain(self) -> str:
        return self._profiles.active_domain()

    def run(
        self,
        query: str,
        domain: str | None = None,
        emit: Emit | None = None,
        revision: str | None = None,
        generate: bool = True,
        trace: bool = False,
    ) -> dict[str, Any]:
        """Полный retrieval-цикл; `revision` — ревизия данных домена (ADR-026).

        Ревизия участвует в epoch-bump кэша и попадает в `done.revision` как
        fingerprint среза для Eval (ADR-015). Hit эпохи `revision` означает, что
        ответ собран по данным именно этой ревизии — одна ревизия в одном ответе.

        `generate=False` — retrieval-only (design.md §6): Context Assembly есть,
        LLM не вызывается, `text=""` и `generation_time_s=0`.

        `trace=True` — диагностический слой 4 (design.md §5.3): в `done["trace"]`
        кладётся список событий этапов; метрики от трассы не зависят и слои 1–3
        пишутся независимо (без `trace` поле отсутствует — контракт не меняется).
        """
        emit = emit or _noop_emit
        if self._closed:
            raise RuntimeError("QueryPipeline закрыт")
        total_started = time.monotonic()
        trace_events: list[dict[str, Any]] = []

        def _trace(payload: dict[str, Any]) -> None:
            if trace:
                trace_events.append(payload)

        emit("status", {"stage": "embedding"})
        active = domain or self._profiles.active_domain()
        embed_started = time.monotonic()
        embedding = self._embedder.embed(query, active)
        _trace(
            {
                "stage": "embedding",
                "ms": round((time.monotonic() - embed_started) * 1000, 1),
                "dimensions": len(embedding),
            }
        )

        profile = self._load_profile(active)
        graph_requested = graph_search_enabled(profile)
        limit = _context_limit(profile)
        enabled = graph_requested
        graph_degraded = False
        projection_status = "not_requested" if not graph_requested else "not_tracked"
        projection_revision: str | None = None
        projection_state: ProjectionState | None = None
        if graph_requested and self._projection_state_store is None and self._projection_state_required:
            enabled = False
            graph_degraded = True
            projection_status = "state_store_unavailable"
        elif graph_requested and self._projection_state_store is not None:
            try:
                projection_state = self._projection_state_store.get(active)
            except Exception:  # noqa: BLE001
                enabled = False
                graph_degraded = True
                projection_status = "state_store_error"
            else:
                config_fingerprint = self._projection_config_fingerprint or "default"
                if projection_state is None:
                    enabled = False
                    graph_degraded = True
                    projection_status = "missing"
                elif revision is None:
                    enabled = False
                    graph_degraded = True
                    projection_status = "revision_unknown"
                elif projection_state.data_revision != revision:
                    enabled = False
                    graph_degraded = True
                    projection_status = "stale"
                elif projection_state.config_fingerprint != config_fingerprint:
                    enabled = False
                    graph_degraded = True
                    projection_status = "config_mismatch"
                elif projection_state.projection_revision != projection_revision_for(
                    revision,
                    config_fingerprint,
                ):
                    enabled = False
                    graph_degraded = True
                    projection_status = "projection_revision_mismatch"
                elif not projection_state.is_ready(revision, config_fingerprint):
                    enabled = False
                    graph_degraded = True
                    projection_status = projection_state.status
                else:
                    try:
                        vector_projection_valid = self._vector_store.verify_projection(
                            active,
                            projection_state.projection_revision,
                        )
                    except Exception:  # noqa: BLE001
                        vector_projection_valid = False
                        projection_status = "projection_verification_error"
                    if not vector_projection_valid:
                        enabled = False
                        graph_degraded = True
                        if projection_status != "projection_verification_error":
                            projection_status = "projection_metadata_mismatch"
                    else:
                        projection_status = "ready"
                        projection_revision = projection_state.projection_revision
        if enabled and self._graph_store is None:
            enabled = False
            graph_degraded = True
            projection_status = "adapter_missing"
        if graph_requested and not enabled:
            _trace(
                {
                    "stage": "graph_readiness",
                    "degraded": True,
                    "status": projection_status,
                    "projection_revision": (
                        projection_state.projection_revision if projection_state is not None else None
                    ),
                }
            )
        graph_cache_ready = (
            graph_requested
            and enabled
            and projection_status == "ready"
            and projection_revision is not None
        )
        cache_allowed = True
        cache_revision = (
            f"{revision or 'default'}:{'graph' if graph_cache_ready else 'vector'}:"
            f"{projection_revision if graph_cache_ready else 'none'}"
        )

        # Semantic Cache (бандл 3/3): hit — до store/LLM, без token-событий и обращений к осям.
        cache_lookup_s = 0.0
        if self._semantic_cache is not None and cache_allowed:
            cache_started = time.monotonic()
            cached = self._semantic_cache.lookup(
                embedding, self._cache_threshold, active, revision=cache_revision
            )
            cache_lookup_s = round(time.monotonic() - cache_started, 3)
            if cached is not None:
                emit("status", {"stage": "cache", "hit": True})
                _trace({"stage": "cache", "hit": True})
                cached_done: dict[str, Any] = {
                    "text": cached.text,
                    "sources": cached.sources,
                    "cache_hit": True,
                    "cache_lookup_s": cache_lookup_s,
                    "generation_time_s": 0.0,
                    "retrieval_time_s": 0.0,
                    "total_time_s": round(time.monotonic() - total_started, 3),
                    "revision": revision,
                    "graph_degraded": graph_degraded,
                    "projection_status": projection_status,
                    "projection_revision": projection_revision,
                }
                if graph_requested and graph_degraded:
                    self.projection_metrics.observe_fallback(
                        projection_status if projection_status != "ready" else "graph_expansion"
                    )
                _trace({"stage": "done", "total_time_s": cached_done["total_time_s"]})
                if trace:
                    cached_done["trace"] = trace_events
                emit("done", cached_done)
                return cached_done

        graph_retriever = (
            GraphRetriever(
                self._graph_store,
                profile,
                max_nodes=self._max_graph_nodes,
                domain=active,
            )
            if self._graph_store is not None
            else None
        )
        vector_retriever = VectorRetriever(
            self._vector_store,
            top_k=self._max_vector_chunks,
            domain=active,
        )

        retrieval_started = time.monotonic()
        emit("status", {"stage": "vector"})
        body_chunks = vector_retriever.retrieve(embedding)
        vector_scores = {
            str(chunk.get("chunk_id")): float(chunk.get("score") or 0.0)
            for chunk in body_chunks
        }
        skeleton_rows: list[dict[str, Any]] = []
        expanded_rows: list[dict[str, Any]] = []
        graph_boosts: dict[str, float] = {}
        if enabled:
            emit("status", {"stage": "graph", "enabled": True})
            retrieval_profile = profile.get("retrieval") or {}
            seed_ids = [
                str(value)
                for chunk in body_chunks
                for key in ("context_ids", "tag_ids")
                for value in (chunk.get(key) or [])
                if value
            ]
            unique_seed_ids = list(dict.fromkeys(seed_ids))
            if graph_retriever is None:
                graph_degraded = True
                _trace({"stage": "graph_expansion", "degraded": True, "reason": "adapter_missing"})
            elif unique_seed_ids:
                try:
                    expansion_kinds_raw = retrieval_profile.get("expansion_kinds")
                    expansion_kinds = (
                        [str(item) for item in expansion_kinds_raw]
                        if isinstance(expansion_kinds_raw, list)
                        else None
                    )
                    expanded_rows = graph_retriever.expand(
                        unique_seed_ids,
                        direction=str(retrieval_profile.get("expansion_direction", "both")),
                        kinds=expansion_kinds,
                        max_depth=int(retrieval_profile.get("max_depth", 2)),
                        max_fanout=int(retrieval_profile.get("max_fanout", 8)),
                        max_nodes=int(retrieval_profile.get("max_graph_nodes", self._max_graph_nodes)),
                    )
                    if not expanded_rows:
                        graph_degraded = True
                        _trace(
                            {
                                "stage": "graph_expansion",
                                "seed_chunk_ids": [chunk.get("chunk_id") for chunk in body_chunks],
                                "context_ids": unique_seed_ids,
                                "paths": [],
                                "degraded": True,
                                "reason": (
                                    "no_matching_edge_kinds"
                                    if expansion_kinds
                                    else "empty_projection"
                                ),
                            }
                        )
                    else:
                        skeleton_rows = [
                            {"n": row, "m": None, "rel_type": row.get("kind", "RELATED")}
                            for row in expanded_rows
                        ]
                        _trace(
                            {
                                "stage": "graph_expansion",
                                "seed_chunk_ids": [chunk.get("chunk_id") for chunk in body_chunks],
                                "context_ids": unique_seed_ids,
                                "paths": [row.get("path", []) for row in expanded_rows],
                                "depths": [row.get("depth", 0) for row in expanded_rows],
                                "origins": [row.get("origin") for row in expanded_rows],
                                "confidences": [row.get("confidence") for row in expanded_rows],
                                "boost": float(retrieval_profile.get("graph_boost", 0.0) or 0.0),
                            }
                        )
                except Exception:  # noqa: BLE001
                    graph_degraded = True
                    _trace({"stage": "graph_expansion", "degraded": True})
            else:
                graph_degraded = True
                _trace(
                    {
                        "stage": "graph_expansion",
                        "context_ids": [],
                        "paths": [],
                        "degraded": True,
                        "reason": "no_context_ids",
                    }
                )

        if enabled and expanded_rows:
            retrieval_profile = profile.get("retrieval") or {}
            try:
                boost = float(retrieval_profile.get("graph_boost", 0.0))
            except (TypeError, ValueError):
                boost = 0.0
            if boost:
                graph_sources = {
                    str(source)
                    for row in expanded_rows
                    for source in (row.get("source_ids") or [])
                    if source
                }
                for chunk in body_chunks:
                    if str(chunk.get("source_url") or "") in graph_sources:
                        graph_boosts[str(chunk.get("chunk_id"))] = boost

        if self._semantic_cache is not None and cache_allowed:
            _trace({"stage": "cache", "hit": False})
        _trace({"stage": "graph", "enabled": enabled, "skeleton_rows": render_skeleton(skeleton_rows)})
        _trace(
            {
                "stage": "vector",
                "candidates": [
                    {
                        "chunk_id": chunk.get("chunk_id"),
                        "rank": index + 1,
                        "score": vector_scores.get(str(chunk.get("chunk_id"))),
                        "score_before": vector_scores.get(str(chunk.get("chunk_id"))),
                    }
                    for index, chunk in enumerate(body_chunks)
                ],
            }
        )

        emit("status", {"stage": "rerank"})
        scores = self._reranker.rerank(query, body_chunks)
        rerank_scores: list[dict[str, Any]] = []
        for chunk, score in zip(body_chunks, scores):
            score_before = float(chunk.get("score") or 0.0)
            chunk_id = str(chunk.get("chunk_id"))
            chunk["score"] = float(score) + graph_boosts.get(chunk_id, 0.0)
            rerank_scores.append(
                {
                    "chunk_id": chunk.get("chunk_id"),
                    "before": round(score_before, 4),
                    "after": round(float(chunk["score"]), 4),
                }
            )
        body_chunks.sort(key=lambda chunk: chunk.get("score", 0.0), reverse=True)
        _trace({"stage": "rerank", "scores": rerank_scores})

        context = ContextAssembly(limit).assemble(skeleton_rows, body_chunks)
        retrieval_s = round(time.monotonic() - retrieval_started, 3)

        prompt = _build_prompt(query, context.text)
        _trace(
            {
                "stage": "llm",
                "prompt_tokens": estimate_tokens(prompt),
                "context_tokens": context.tokens,
                "dropped_chunks": context.dropped,
            }
        )

        if generate:
            emit("status", {"stage": "llm"})
            started = time.monotonic()
            parts: list[str] = []
            for delta in self._llm.generate(prompt, system=DEFAULT_SYSTEM_PROMPT, stream=True):
                parts.append(delta)
                emit("token", {"delta": delta})
            text = "".join(parts)
            generation_s = round(time.monotonic() - started, 3)
        else:
            text = ""
            generation_s = 0.0

        sources = build_sources(body_chunks, skeleton_rows)
        if graph_requested and graph_degraded:
            self.projection_metrics.observe_fallback(
                projection_status if projection_status != "ready" else "graph_expansion"
            )
        cache_store_allowed = not graph_requested or not enabled or not graph_degraded
        if generate and self._semantic_cache is not None and cache_allowed and cache_store_allowed:
            # store() сам отклоняет «плохие» ответы (пустой text, отказ LLM) — решение 2026-09-13.
            self._semantic_cache.store(
                embedding,
                CachedAnswer(text=text, sources=sources),
                active,
                revision=cache_revision,
            )
        done: dict[str, Any] = {
            "text": text,
            "sources": sources,
            "generation_time_s": generation_s,
            "retrieval_time_s": retrieval_s,
            "total_time_s": round(time.monotonic() - total_started, 3),
            "revision": revision,
            "graph_degraded": graph_degraded,
            "projection_status": projection_status,
            "projection_revision": projection_revision,
        }
        if self._semantic_cache is not None and cache_allowed:
            done["cache_hit"] = False
            done["cache_lookup_s"] = cache_lookup_s
        _trace({"stage": "done", "total_time_s": done["total_time_s"]})
        if trace:
            done["trace"] = trace_events
        emit("done", done)
        return done

    def _load_profile(self, domain: str) -> dict[str, Any]:
        try:
            profile = self._profiles.load(domain)
            if self._strict_profile:
                _validate_runtime_profile(profile)
            return profile
        except ProfileError:
            if self._strict_profile:
                raise
            return {"retrieval": {}, "context_assembly": {}, "ontology": {}}


def _context_limit(profile: dict[str, Any]) -> int:
    try:
        value = int((profile.get("context_assembly") or {}).get("max_tokens", CONTEXT_TOKEN_LIMIT))
    except (TypeError, ValueError):
        return CONTEXT_TOKEN_LIMIT
    return value if value > 0 else CONTEXT_TOKEN_LIMIT


def _build_prompt(query: str, context_text: str) -> str:
    return (
        f"Контекст:\n{context_text}\n\n"
        f"Вопрос: {query}\n\n"
        "Ответ:"
    )