"""InMemory-реализации GraphStoreProvider/VectorStoreProvider — контрактные двойники
для тестов и демо без внешних систем (L1-02, ADR-012: контрактные тесты обязательны).

Поддерживаемое подмножество Cypher в `InMemoryGraphStore.query` (ограниченная грамматика,
достаточный для шаблона ретривера из Domain Profile):

    MATCH (n)
    WHERE any(t IN $terms WHERE toLower(n.<prop>) CONTAINS toLower(t) OR ...)
    [OPTIONAL MATCH (n)-[r]-(m) [WHERE m:<Label> OR m:<Label> ...]]
    RETURN n, m, type(r) AS rel_type
    LIMIT $max_nodes

Всё, что выходит за подмножество, даёт NotImplementedError (не молчаливый отказ).
`transaction()` — атомарная запись пачки операций с откатом при ошибке (L2-04).
"""

from __future__ import annotations

import math
import re
from contextlib import contextmanager
from typing import Any

from graphrag_proto.retrieval.adapters.base import GraphStoreProvider, VectorStoreProvider

_TERM_COND_RE = re.compile(r"toLower\(n\.(\w+)\)\s+CONTAINS\s+toLower\(t\)")
_LABEL_COND_RE = re.compile(r"m:(\w+)")


class _Journal:
    """Буфер операций для атомарной записи (L2-04): применяется на выходе успеха."""

    def __init__(self) -> None:
        self.ops: list[tuple[str, Any]] = []

    def record(self, kind: str, arg: Any) -> None:
        self.ops.append((kind, arg))


class InMemoryGraphStore(GraphStoreProvider):
    def __init__(self) -> None:
        self._nodes: dict[str, dict[str, Any]] = {}
        self._edges: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._journal: _Journal | None = None

    # ------------------------------------------------------------- journaled ops

    def _mutate(self, kind: str, arg: Any) -> None:
        if self._journal is not None:
            self._journal.record(kind, arg)
            return
        self._do(kind, arg)

    def _do(self, kind: str, arg: Any) -> None:
        if kind == "upsert_nodes":
            for node in arg:
                self._nodes[node["node_id"]] = {
                    "labels": list(node.get("labels") or []),
                    "properties": dict(node.get("properties") or {}),
                }
        elif kind == "upsert_edges":
            for edge in arg:
                key = (edge["from_id"], edge["to_id"], edge["type"])
                self._edges[key] = dict(edge.get("properties") or {})
        elif kind == "delete_node":
            self._nodes.pop(arg, None)
            for key in [k for k in self._edges if arg in (k[0], k[1])]:
                self._edges.pop(key, None)
        elif kind == "delete_edges_by_source":
            self._edges = {
                key: props
                for key, props in self._edges.items()
                if not (key[0] == arg and key[2] == "CONTAINS")
            }
        else:
            raise NotImplementedError(f"неизвестная операция: {kind!r}")

    @contextmanager
    def transaction(self) -> Any:
        prev = self._journal
        self._journal = _Journal()
        try:
            yield self
        except BaseException:
            self._journal = prev
            raise
        else:
            journal = self._journal
            self._journal = prev
            for kind, arg in journal.ops:
                self._do(kind, arg)

    # ------------------------------------------------------------- API (ABC)

    def query(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        params = params or {}
        root = _match_root(cypher)
        if root != "n":
            raise NotImplementedError(f"InMemoryGraphStore: некорректный MATCH root {root!r}")
        terms = _terms_of(params)
        props = list(_TERM_COND_RE.findall(cypher))
        if not props:
            raise NotImplementedError("InMemoryGraphStore: WHERE-кондиция не распознана")
        allowed_labels = list(_LABEL_COND_RE.findall(cypher))
        limit = int(params["max_nodes"]) if params.get("max_nodes") else None

        rows: list[dict[str, Any]] = []
        for node_id, node in sorted(self._nodes.items()):
            if not self._matches(node, terms, props):
                continue
            neighbors = self._neighbors(node_id, allowed_labels)
            if neighbors:
                for other_id, rel_type in neighbors:
                    rows.append(
                        {
                            "n": self._node_view(node_id),
                            "m": self._node_view(other_id),
                            "rel_type": rel_type,
                        }
                    )
            else:
                rows.append({"n": self._node_view(node_id), "m": None, "rel_type": None})
            if limit is not None and len(rows) >= limit:
                break
        return rows

    def upsert_nodes(self, nodes: list[dict[str, Any]]) -> None:
        self._mutate("upsert_nodes", nodes)

    def upsert_edges(self, edges: list[dict[str, Any]]) -> None:
        self._mutate("upsert_edges", edges)

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        node = self._nodes.get(node_id)
        return self._node_view(node_id) if node else None

    def delete_node(self, node_id: str) -> bool:
        if self._nodes.get(node_id) is None:
            return self._journal is not None
        self._mutate("delete_node", node_id)
        return True

    def list_chunk_ids_of_source(self, source_id: str) -> list[str]:
        return sorted(to_id for (from_id, to_id, etype) in self._edges if from_id == source_id and etype == "CONTAINS")

    # ------------------------------------------------------------- internals

    def _matches(self, node: dict[str, Any], terms: list[str], props: list[str]) -> bool:
        if not terms:
            return False
        values = [str(node["properties"].get(prop, "")) for prop in props]
        for term in terms:
            needle = term.lower()
            if any(needle in value.lower() for value in values):
                return True
        return False

    def _neighbors(self, node_id: str, allowed_labels: list[str]) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        for (from_id, to_id, etype) in self._edges:
            other: str | None = None
            if from_id == node_id:
                other = to_id
            elif to_id == node_id:
                other = from_id
            if other is None:
                continue
            node = self._nodes.get(other)
            if node is None:
                continue
            if allowed_labels and not (set(allowed_labels) & set(node["labels"])):
                continue
            result.append((other, etype))
        return result

    def _node_view(self, node_id: str) -> dict[str, Any]:
        node = self._nodes[node_id]
        return {"_node_id": node_id, "_labels": list(node["labels"]), **dict(node["properties"])}


def _match_root(cypher: str) -> str:
    match = re.match(r"MATCH\s*\(\s*(\w+)\s*\)", cypher)
    if not match:
        raise NotImplementedError("InMemoryGraphStore: ожидалось «MATCH (n)»")
    return match.group(1)


def _terms_of(params: dict[str, Any]) -> list[str]:
    terms = params.get("terms")
    if terms is None:
        raise NotImplementedError("InMemoryGraphStore: параметр $terms обязателен")
    if isinstance(terms, str):
        return [terms]
    if isinstance(terms, list) and all(isinstance(t, str) for t in terms):
        return list(terms)
    raise NotImplementedError(f"InMemoryGraphStore: $terms имеет неожиданный тип {type(terms)!r}")


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class InMemoryVectorStore(VectorStoreProvider):
    def __init__(self) -> None:
        self._vectors: dict[str, dict[str, Any]] = {}
        self._journal: _Journal | None = None

    def _mutate(self, kind: str, arg: Any) -> None:
        if self._journal is not None:
            self._journal.record(kind, arg)
            return
        self._do(kind, arg)

    def _do(self, kind: str, arg: Any) -> None:
        if kind == "upsert_vectors":
            for item in arg:
                self._vectors[item["chunk_id"]] = item
        elif kind == "delete_vectors":
            for chunk_id in arg:
                self._vectors.pop(chunk_id, None)
        else:
            raise NotImplementedError(f"неизвестная операция: {kind!r}")

    @contextmanager
    def transaction(self) -> Any:
        prev = self._journal
        self._journal = _Journal()
        try:
            yield self
        except BaseException:
            self._journal = prev
            raise
        else:
            journal = self._journal
            self._journal = prev
            for kind, arg in journal.ops:
                self._do(kind, arg)

    def upsert_vectors(self, items: list[dict[str, Any]]) -> None:
        self._mutate("upsert_vectors", items)

    def delete_vectors(self, chunk_ids: list[str]) -> None:
        self._mutate("delete_vectors", chunk_ids)

    def vector_search(self, embedding: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        scored: list[tuple[float, str]] = []
        for chunk_id, item in self._vectors.items():
            score = _cosine(embedding, item.get("embedding") or [])
            if score > 0.0:
                scored.append((score, chunk_id))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        result: list[dict[str, Any]] = []
        for score, chunk_id in scored[: (top_k if top_k > 0 else 5)]:
            item = self._vectors[chunk_id]
            row = {"chunk_id": chunk_id, "score": round(score, 6)}
            row.update(item.get("metadata") or {})
            result.append(row)
        return result