"""Боевые хранилища M2: Neo4jGraphStore / Neo4jVectorStore (bolt://neo4j:7687).

Векторный поиск M2 — косинусная близость по свойству `embedding` узлов :Chunk
(Cypher-выборка + cosine в Python; native vector index — M3, ADR-023 OQ2).

Драйвер `neo4j` импортируется лениво: модуль грузится и в окружениях без драйвера.
Динамические имена рёбер проходят валидацию (типы из Domain Profile, не пользователь).
"""

from __future__ import annotations

import math
import re
from contextlib import contextmanager
from typing import Any

from graphrag_proto.retrieval.adapters.base import GraphStoreProvider, VectorStoreProvider
from graphrag_proto.retrieval.adapters.schemas import (
    normalize_graph_row,
    normalize_vector_row,
)

_SAFE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

CHUNK_LABEL = "Chunk"
SOURCE_LABEL = "Source"
ENTITY_LABEL = "Entity"


def _safe_type(name: str) -> str:
    if not _SAFE_IDENT_RE.match(name):
        raise ValueError(f"небезопасное имя типа/рёбра: {name!r}")
    return name


def _log_error(exc_base: str, exc: Exception) -> RuntimeError:
    return RuntimeError(f"{exc_base}: {exc}")


class Neo4jGraphStore(GraphStoreProvider):
    """Графовая ось поверх Neo4j (ADR-013)."""

    def __init__(self, uri: str, user: str, password: str, database: str | None = None) -> None:
        self._uri = uri
        self._user = user
        self._password = password
        self._database = database
        self._driver: Any = None

    def _get_driver(self) -> Any:
        if self._driver is None:
            from neo4j import GraphDatabase

            self._driver = GraphDatabase.driver(self._uri, auth=(self._user, self._password))
        return self._driver

    def _session(self) -> Any:
        if self._database:
            return self._get_driver().session(database=self._database)
        return self._get_driver().session()

    def query(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self._session() as session:
            records = session.run(cypher, parameters=params or {}).data()
        return [normalize_graph_row(row) for row in records]

    def upsert_nodes(self, nodes: list[dict[str, Any]]) -> None:
        with self._session() as session:
            _upsert_nodes(session, nodes)

    def upsert_edges(self, edges: list[dict[str, Any]]) -> None:
        with self._session() as session:
            _upsert_edges(session, edges)

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        with self._session() as session:
            row = session.run(
                "MATCH (n {node_id: $node_id}) RETURN n", parameters={"node_id": node_id}
            ).data()
        return normalize_graph_row({"n": row[0]["n"]}) if row else None

    def delete_node(self, node_id: str) -> bool:
        with self._session() as session:
            summary = session.run(
                "MATCH (n {node_id: $node_id}) DETACH DELETE n RETURN count(n) AS c",
                parameters={"node_id": node_id},
            ).single()
        return bool(summary and summary["c"] > 0)

    def list_chunk_ids_of_source(self, source_id: str) -> list[str]:
        with self._session() as session:
            rows = session.run(
                "MATCH (s {node_id: $source_id})-[:CONTAINS]->(c) RETURN c.node_id AS chunk_id",
                parameters={"source_id": source_id},
            ).data()
        return [str(row["chunk_id"]) for row in rows]

    @contextmanager
    def transaction(self) -> Any:
        buf = _TxGraph(self, "graph")
        try:
            yield buf
            buf.commit()
        except BaseException:
            buf.discard()
            raise

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None


class _TxGraph(GraphStoreProvider):
    """Буфер записей в одной транзакции Neo4j (L2-04): rollback при ошибке."""

    def __init__(self, outer: Neo4jGraphStore, kind: str) -> None:
        self._outer = outer
        self._kind = kind
        self._ops: list[tuple[str, Any]] = []

    def _buffered(self, kind: str, arg: Any) -> None:
        self._ops.append((kind, arg))

    def upsert_nodes(self, nodes: list[dict[str, Any]]) -> None:
        self._buffered("upsert_nodes", nodes)

    def upsert_edges(self, edges: list[dict[str, Any]]) -> None:
        self._buffered("upsert_edges", edges)

    def delete_node(self, node_id: str) -> bool:
        self._buffered("delete_node", node_id)
        return True

    def query(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError("чтение в транзакции записи не поддерживается")

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        raise NotImplementedError("чтение в транзакции записи не поддерживается")

    def list_chunk_ids_of_source(self, source_id: str) -> list[str]:
        raise NotImplementedError("чтение в транзакции записи не поддерживается")

    def commit(self) -> None:
        if not self._ops:
            return
        with self._outer._session() as session, session.begin_transaction() as tx:
            for kind, arg in self._ops:
                if kind == "upsert_nodes":
                    _upsert_nodes(tx, arg)
                elif kind == "upsert_edges":
                    _upsert_edges(tx, arg)
                elif kind == "delete_node":
                    _delete_node(tx, arg)
            # commit в implicit begin_transaction по выходу из with
            _rollback_guard = tx
            del _rollback_guard

    def discard(self) -> None:
        self._ops = []


def _delete_node(runner: Any, node_id: str) -> None:
    runner.run(
        "MATCH (n {node_id: $node_id}) DETACH DELETE n",
        parameters={"node_id": node_id},
    ).consume()


def _upsert_nodes(runner: Any, nodes: list[dict[str, Any]]) -> None:
    for node in nodes:
        node_id = node["node_id"]
        for label in node.get("labels") or [ENTITY_LABEL]:
            _safe_type(label)
        labels = "".join(f":{_safe_type(l)}" for l in node.get("labels") or [ENTITY_LABEL])
        runner.run(
            f"MERGE (n {{node_id: $node_id}}) SET n{labels} SET n += $props",
            parameters={"node_id": node_id, "props": node.get("properties") or {}},
        ).consume()


def _upsert_edges(runner: Any, edges: list[dict[str, Any]]) -> None:
    for edge in edges:
        rel_type = _safe_type(edge["type"])
        runner.run(
            f"MATCH (a {{node_id: $from}}) MATCH (b {{node_id: $to}}) "
            f"MERGE (a)-[r:{rel_type}]->(b) SET r += $props",
            parameters={
                "from": edge["from_id"],
                "to": edge["to_id"],
                "type": edge["type"],
                "props": edge.get("properties") or {},
            },
        ).consume()


class Neo4jVectorStore(VectorStoreProvider):
    """Векторная ось поверх Neo4j (M2: косинус по свойству embedding)."""

    def __init__(self, uri: str, user: str, password: str, database: str | None = None) -> None:
        self._uri = uri
        self._user = user
        self._password = password
        self._database = database
        self._driver: Any = None

    def _get_driver(self) -> Any:
        if self._driver is None:
            from neo4j import GraphDatabase

            self._driver = GraphDatabase.driver(self._uri, auth=(self._user, self._password))
        return self._driver

    def _session(self) -> Any:
        if self._database:
            return self._get_driver().session(database=self._database)
        return self._get_driver().session()

    def upsert_vectors(self, items: list[dict[str, Any]]) -> None:
        with self._session() as session:
            _upsert_vectors(session, items)

    def delete_vectors(self, chunk_ids: list[str]) -> None:
        with self._session() as session:
            if chunk_ids:
                session.run(
                    f"MATCH (c:{CHUNK_LABEL}) WHERE c.node_id IN $ids DETACH DELETE c",
                    parameters={"ids": chunk_ids},
                ).consume()

    def vector_search(self, embedding: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        with self._session() as session:
            rows = session.run(
                f"MATCH (c:{CHUNK_LABEL}) WHERE EXISTS(c.embedding) "
                "RETURN c.node_id AS chunk_id, c.embedding AS embedding, c",
                parameters={},
            ).data()
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            emb = row.get("embedding") or []
            score = _cosine(embedding, list(emb))
            if score > 0.0:
                row["score"] = score
                scored.append((score, row))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        result = [
            normalize_vector_row(row_with_score)
            for score, row_with_score in scored[: (top_k if top_k > 0 else 5)]
        ]
        return result

    @contextmanager
    def transaction(self) -> Any:
        buf = _TxVector(self, "vector")
        try:
            yield buf
            buf.commit()
        except BaseException:
            buf.discard()
            raise

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None


class _TxVector(VectorStoreProvider):
    """Буфер записей векторов в одной транзакции Neo4j (L2-04)."""

    def __init__(self, outer: Neo4jVectorStore, kind: str) -> None:
        self._outer = outer
        self._kind = kind
        self._ops: list[tuple[str, Any]] = []

    def upsert_vectors(self, items: list[dict[str, Any]]) -> None:
        self._ops.append(("upsert_vectors", items))

    def delete_vectors(self, chunk_ids: list[str]) -> None:
        self._ops.append(("delete_vectors", chunk_ids))

    def vector_search(self, embedding: list[float], top_k: int = 5) -> list[dict[str, Any]]:
        raise NotImplementedError("чтение в транзакции записи не поддерживается")

    def commit(self) -> None:
        if not self._ops:
            return
        with self._outer._session() as session, session.begin_transaction() as tx:
            for kind, arg in self._ops:
                if kind == "upsert_vectors":
                    _upsert_vectors(tx, arg)
                elif kind == "delete_vectors" and arg:
                        tx.run(
                            f"MATCH (c:{CHUNK_LABEL}) WHERE c.node_id IN $ids DETACH DELETE c",
                            parameters={"ids": arg},
                        ).consume()
            del tx

    def discard(self) -> None:
        self._ops = []


def _upsert_vectors(runner: Any, items: list[dict[str, Any]]) -> None:
    for item in items:
        chunk_id = item["chunk_id"]
        meta = item.get("metadata") or {}
        runner.run(
            f"MERGE (c:{CHUNK_LABEL} {{node_id: $chunk_id}}) "
            "SET c.chunk_id = $chunk_id, c.embedding = $embedding, c += $props",
            parameters={
                "chunk_id": chunk_id,
                "embedding": item.get("embedding") or [],
                "props": meta,
            },
        ).consume()


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)