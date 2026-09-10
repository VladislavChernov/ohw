"""Графовая и векторная оси поиска (L1-04: независимы до Context Assembly).

GraphRetriever выполняет параметризованный Cypher-шаблон активного Domain Profile
(L1-01): типы узлов подставляются в `{node_labels}` из `ontology.node_types`.
VectorRetriever — `vector_search(top_k=5)` по эмбеддингу запроса (ADR-013).
"""

from __future__ import annotations

import re
from typing import Any

from graphrag_proto.retrieval.adapters.base import GraphStoreProvider, VectorStoreProvider

_TERM_RE = re.compile(r"[\wА-Яа-яЁё-]{3,}")

NODE_LABEL_PLACEHOLDER = "{node_labels}"

DEFAULT_TEMPLATE = (
    "MATCH (n)\n"
    "WHERE any(t IN $terms WHERE toLower(n.canonical_name) CONTAINS toLower(t) "
    "OR toLower(n.name) CONTAINS toLower(t) OR toLower(n.id) CONTAINS toLower(t))\n"
    "OPTIONAL MATCH (n)-[r]-(m)\n"
    "WHERE m:{node_labels}\n"
    "RETURN n, m, type(r) AS rel_type\n"
    "LIMIT $max_nodes"
)


def extract_query_terms(query: str) -> list[str]:
    """Термины для графового матчинга: само сообщение + слова >= 3 символов."""
    words = [w.casefold() for w in _TERM_RE.findall(query)]
    candidates = [query.casefold().strip(), *words]
    terms = list(dict.fromkeys([t for t in candidates if t]))
    return terms


class GraphRetriever:
    """Графовая ось: шаблон из профиля -> store.query -> «скелет» контекста."""

    def __init__(self, graph_store: GraphStoreProvider, profile: dict[str, Any], max_nodes: int = 5) -> None:
        self._graph_store = graph_store
        self._max_nodes = max_nodes
        self.cypher, self.node_labels = build_cypher(profile)

    def retrieve(self, query: str) -> list[dict[str, Any]]:
        if not self.node_labels:
            return []
        terms = extract_query_terms(query)
        if not terms:
            return []
        # Ось отключается целиком до вызова здесь — тумблер читает QueryPipeline
        # (`graph_search_enabled` из profile.retrieval / env RETRIEVAL_GRAPH_ENABLED).
        try:
            rows = self._graph_store.query(
                self.cypher,
                {"terms": terms, "max_nodes": self._max_nodes},
            )
        except NotImplementedError:
            return []
        except Exception:  # noqa: BLE001 - обход графа не должен валить Pipeline
            return []
        return [row for row in rows if row.get("n")]


class VectorRetriever:
    """Векторная ось: top_k ближайших чанков по эмбеддингу запроса."""

    def __init__(self, vector_store: VectorStoreProvider, top_k: int = 5) -> None:
        self._vector_store = vector_store
        self._top_k = top_k

    def retrieve(self, embedding: list[float]) -> list[dict[str, Any]]:
        return list(self._vector_store.vector_search(embedding, top_k=self._top_k))


def build_cypher(profile: dict[str, Any]) -> tuple[str, list[str]]:
    """Cypher-шаблон ретривера из Domain Profile (L1-01), node_labels — из онтологии."""
    retrieval = profile.get("retrieval") or {}
    ontology = profile.get("ontology") or {}
    node_types = ontology.get("node_types") or []
    labels = [
        str(t["type"]) if isinstance(t, dict) and isinstance(t.get("type"), str) else ""
        for t in node_types
    ]
    labels = [label for label in labels if label]
    template = retrieval.get("cypher_template") or DEFAULT_TEMPLATE
    if not isinstance(template, str) or not template.strip():
        template = DEFAULT_TEMPLATE
    cypher = template.replace(NODE_LABEL_PLACEHOLDER, " OR m:".join(labels))
    return cypher, labels