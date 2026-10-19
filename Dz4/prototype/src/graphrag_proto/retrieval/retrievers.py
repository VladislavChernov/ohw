"""Vector baseline и optional graph experiment retrieval.

GraphRetriever.expand() получает IDs из vector metadata и возвращает bounded generic
context rows. Legacy query() оставлен только для старых adapters и не является target-путём.
VectorRetriever — `vector_search(top_k=5)` по эмбеддингу запроса.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Sequence
from typing import Any

from graphrag_proto.retrieval.adapters.base import GraphStoreProvider, VectorStoreProvider

_TERM_RE = re.compile(r"[\wА-Яа-яЁё-]{3,}")

def extract_query_terms(query: str) -> list[str]:
    """Термины для графового матчинга: само сообщение + слова >= 3 символов."""
    words = [w.casefold() for w in _TERM_RE.findall(query)]
    candidates = [query.casefold().strip(), *words]
    terms = list(dict.fromkeys([t for t in candidates if t]))
    return terms


def _has_active_provenance(node: object) -> bool:
    if not isinstance(node, dict):
        return True
    for key in ("source_ids", "chunk_ids"):
        value = node.get(key)
        if key in node and isinstance(value, list) and not value:
            return False
    return True


def _normalize_vector_row(row: object) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    metadata = row.get("metadata")
    if isinstance(metadata, dict):
        normalized = {**metadata, **row}
        normalized.pop("metadata", None)
        return normalized
    return dict(row)


class GraphRetriever:
    """Optional graph experiment: vector context IDs -> bounded generic expansion."""

    def __init__(
        self,
        graph_store: GraphStoreProvider,
        profile: dict[str, Any],
        max_nodes: int = 5,
        domain: str | None = None,
    ) -> None:
        self._graph_store = graph_store
        self._max_nodes = max_nodes
        self._domain = domain

    def expand(
        self,
        context_ids: list[str],
        *,
        direction: str = "both",
        kinds: Sequence[str] | None = None,
        max_depth: int = 2,
        max_fanout: int = 8,
        max_nodes: int = 32,
    ) -> list[dict[str, Any]]:
        if not context_ids:
            return []
        rows = self._graph_store.expand(
            context_ids,
            direction=direction,
            kinds=kinds,
            max_depth=max_depth,
            max_fanout=max_fanout,
            max_nodes=max_nodes,
        )
        if self._domain:
            rows = [
                row
                for row in rows
                if str(row.get("domain") or "") == self._domain
                or str(row.get("node_id") or "").startswith(f"tag:{self._domain}:")
            ]
        return [row for row in rows if _has_active_provenance(row)]

class VectorRetriever:
    """Векторная ось: top_k ближайших чанков по эмбеддингу запроса."""

    def __init__(
        self,
        vector_store: VectorStoreProvider,
        top_k: int = 5,
        domain: str | None = None,
    ) -> None:
        self._vector_store = vector_store
        self._top_k = top_k
        self._domain = domain

    def retrieve(self, embedding: list[float]) -> list[dict[str, Any]]:
        supports_domain = False
        if self._domain is not None:
            try:
                parameters = inspect.signature(self._vector_store.vector_search).parameters
                supports_domain = "domain" in parameters or any(
                    parameter.kind is inspect.Parameter.VAR_KEYWORD
                    for parameter in parameters.values()
                )
            except (TypeError, ValueError):
                supports_domain = False
        if self._domain is not None and not supports_domain:
            raise NotImplementedError(
                "vector adapter must support domain filtering for active-domain retrieval"
            )
        if supports_domain:
            result = self._vector_store.vector_search(
                embedding,
                top_k=self._top_k,
                domain=self._domain,
            )
        else:
            result = self._vector_store.vector_search(
                embedding,
                top_k=self._top_k if self._domain is None else max(self._top_k, 100),
            )
        normalized = [row for item in result if (row := _normalize_vector_row(item)) is not None]
        if self._domain is not None:
            normalized = [row for row in normalized if row.get("domain") == self._domain]
        return normalized[: self._top_k]


