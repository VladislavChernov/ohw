"""Реранкеры M2: NoOp (документация: docs/03_retriever.md §1, шаг 4).

NoOp возвращает текущие скоры чанков — порядок не меняется (штатный режим M2).
"""

from __future__ import annotations

from typing import Any

from graphrag_proto.retrieval.adapters.base import Reranker


class NoOpRerankerAdapter(Reranker):
    """Отключённый реранкер: порядок как есть, скор = текущий score чанка."""

    def rerank(self, query: str, chunks: list[dict[str, Any]]) -> list[float]:
        return [float(chunk.get("score") or 0.0) for chunk in chunks]