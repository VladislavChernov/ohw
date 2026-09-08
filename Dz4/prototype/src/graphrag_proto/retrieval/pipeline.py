"""QueryPipeline — 7 шагов retrieval-цикла (docs/03_retriever.md, D3 design M2):

1. embedding (DeterministicEmbedder, L4-01) → status: embedding
2. graph ∥ vector — параллельно, независимые оси (L1-04) → status: graph, vector
3. rerank (NoOp) → status: rerank
4. Context Assembly (context.py, L3-03/L3-04)
5. LLM streaming (OpenAICompatibleAdapter / FakeLLM) → status: llm, token*
6. done — {text, sources, generation_time_s}

emit(event_type, payload) — обратный вызов воркера (публикует события конверта ADR-016).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from graphrag_proto.retrieval.adapters.base import (
    Embedder,
    GraphStoreProvider,
    LLMInference,
    Reranker,
    VectorStoreProvider,
)
from graphrag_proto.retrieval.context import CONTEXT_TOKEN_LIMIT, ContextAssembly
from graphrag_proto.retrieval.profile import DomainProfileLoader, ProfileError
from graphrag_proto.retrieval.retrievers import GraphRetriever, VectorRetriever

Emit = Callable[[str, dict[str, Any]], None]

DEFAULT_SYSTEM_PROMPT = (
    "Ты — GraphRAG-ассистент. Отвечай строго по предоставленному контексту. "
    "Ссылайся на источники из контекста. Если контекста недостаточно — так и скажи."
)


def _noop_emit(event_type: str, payload: dict[str, Any]) -> None:
    return None


def build_sources(body_chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Уникальные sources с максимальным relevance (по source_url)."""
    best: dict[str, float] = {}
    for chunk in body_chunks:
        source = str(chunk.get("source_url") or "")
        if not source:
            continue
        score = float(chunk.get("score") or 0.0)
        best[source] = max(best.get(source, 0.0), score)
    return [{"source_url": source, "relevance": round(score, 4)} for source, score in sorted(best.items(), key=lambda pair: pair[1], reverse=True)]


class QueryPipeline:
    def __init__(
        self,
        embedder: Embedder,
        graph_store: GraphStoreProvider,
        vector_store: VectorStoreProvider,
        reranker: Reranker,
        llm: LLMInference,
        profile_loader: DomainProfileLoader | None = None,
        max_graph_nodes: int = 5,
        max_vector_chunks: int = 5,
    ) -> None:
        self._embedder = embedder
        self._graph_store = graph_store
        self._vector_store = vector_store
        self._reranker = reranker
        self._llm = llm
        self._profiles = profile_loader or DomainProfileLoader()
        self._max_graph_nodes = max_graph_nodes
        self._max_vector_chunks = max_vector_chunks

    def run(self, query: str, domain: str | None = None, emit: Emit | None = None) -> dict[str, Any]:
        emit = emit or _noop_emit
        emit("status", {"stage": "embedding"})
        active = domain or self._profiles.active_domain()
        embedding = self._embedder.embed(query, active)

        profile = self._load_profile(active)
        limit = _context_limit(profile)

        graph_retriever = GraphRetriever(self._graph_store, profile, max_nodes=self._max_graph_nodes)
        vector_retriever = VectorRetriever(self._vector_store, top_k=self._max_vector_chunks)

        emit("status", {"stage": "graph"})
        emit("status", {"stage": "vector"})
        with ThreadPoolExecutor(max_workers=2) as pool:
            future_graph = pool.submit(graph_retriever.retrieve, query)
            future_vector = pool.submit(vector_retriever.retrieve, embedding)
            skeleton_rows = future_graph.result()
            body_chunks = future_vector.result()

        emit("status", {"stage": "rerank"})
        scores = self._reranker.rerank(query, body_chunks)
        for chunk, score in zip(body_chunks, scores):
            chunk["score"] = float(score)
        body_chunks.sort(key=lambda chunk: chunk.get("score", 0.0), reverse=True)

        context = ContextAssembly(limit).assemble(skeleton_rows, body_chunks)

        prompt = _build_prompt(query, context.text)
        emit("status", {"stage": "llm"})
        started = time.monotonic()
        parts: list[str] = []
        for delta in self._llm.generate(prompt, system=DEFAULT_SYSTEM_PROMPT, stream=True):
            parts.append(delta)
            emit("token", {"delta": delta})
        text = "".join(parts)
        generation_s = round(time.monotonic() - started, 3)

        done = {
            "text": text,
            "sources": build_sources(body_chunks),
            "generation_time_s": generation_s,
        }
        emit("done", done)
        return done

    def _load_profile(self, domain: str) -> dict[str, Any]:
        try:
            return self._profiles.load(domain)
        except ProfileError:
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