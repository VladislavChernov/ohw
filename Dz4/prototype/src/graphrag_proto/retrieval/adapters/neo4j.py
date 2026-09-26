"""Боевые хранилища M2: Neo4jGraphStore / Neo4jVectorStore (bolt://neo4j:7687).

Векторный поиск M2 — косинусная близость по свойству `embedding` узлов :Chunk
(Cypher-выборка + cosine в Python; native vector index — M3, ADR-023 OQ2).

Драйвер `neo4j` импортируется лениво: модуль грузится и в окружениях без драйвера.
Динамические имена рёбер проходят техническую валидацию; Domain Profile не является
ontology whitelist.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from contextlib import contextmanager
from typing import Any

from graphrag_proto.retrieval.adapters.base import (
    VECTOR_METADATA_BACKFILL_KEYS,
    Consistency,
    GraphStoreProvider,
    VectorStoreProvider,
    _expand_direction,
    _expand_kinds,
)
from graphrag_proto.retrieval.adapters.schemas import (
    normalize_graph_row,
    normalize_vector_row,
)

_SAFE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

CHUNK_LABEL = "Chunk"
SOURCE_LABEL = "Source"
ENTITY_LABEL = "Entity"
_PROVENANCE_KEYS = ("source_ids", "chunk_ids", "variants", "aliases")


def _safe_type(name: str) -> str:
    if not _SAFE_IDENT_RE.match(name):
        raise ValueError(f"небезопасное имя типа/рёбра: {name!r}")
    return name


def _log_error(exc_base: str, exc: Exception) -> RuntimeError:
    return RuntimeError(f"{exc_base}: {exc}")


_def_transient_aware = True


def _is_transient_exc(exc: BaseException) -> bool:
    """Транзиент-классификация Neo4j (ADR-028): deadlock/перезапуск/сеть.

    Развёртка цепочки причин (UC12-02): проверяется сам `exc` и его `__cause__`,
    чтобы обёртки (`RuntimeError`/`CommitStageError` на границе адаптера и
    CommitStage) не ломали классификацию. Циклы `__cause__` отсекаются.
    Импорт вендорских исключений остаётся здесь — это граница адаптера (ADR-012),
    ядро (L1-02) вендора не знает."""
    try:
        from neo4j.exceptions import ServiceUnavailable, TransientError
    except ImportError:
        return False
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (TransientError, ServiceUnavailable)):
            return True
        current = current.__cause__
    return False


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
        if not row:
            return None
        normalized = normalize_graph_row({"n": row[0]["n"]})
        node = normalized.get("n")
        return node if isinstance(node, dict) else None

    def verify_edge(self, from_id: str, to_id: str, edge_type: str) -> bool:
        rel_type = _safe_type(edge_type)
        with self._session() as session:
            row = session.run(
                f"MATCH (a {{node_id: $from_id}})-[r:{rel_type}]->(b {{node_id: $to_id}}) "
                "RETURN count(r) AS count",
                parameters={"from_id": from_id, "to_id": to_id},
            ).single()
        return bool(row and int(row["count"] or 0) > 0)

    def delete_node(self, node_id: str) -> bool:
        with self._session() as session:
            summary = session.run(
                "MATCH (n {node_id: $node_id}) DETACH DELETE n RETURN count(n) AS c",
                parameters={"node_id": node_id},
            ).single()
        return bool(summary and summary["c"] > 0)

    def remove_source_from_entities(
        self,
        domain: str,
        source_url: str,
        chunk_ids: list[str],
    ) -> None:
        with self._session() as session:
            _remove_source(
                session,
                {"domain": domain, "source_url": source_url, "chunk_ids": chunk_ids},
            )

    def list_chunk_ids_of_source(self, source_id: str) -> list[str]:
        with self._session() as session:
            rows = session.run(
                "MATCH (s {node_id: $source_id})-[:CONTAINS]->(c) RETURN c.node_id AS chunk_id",
                parameters={"source_id": source_id},
            ).data()
        return [str(row["chunk_id"]) for row in rows]

    def ensure_schema(self, node_types: list[dict[str, Any]]) -> None:
        """Schema-провижининг (стадия 1, design.md §0): `CREATE CONSTRAINT ... IS
        UNIQUE` по паре `(domain, unique_key)` каждого типа онтологии. Идемпотентно
        по имени constraint; fallback-типы `Source`/`Entity`/`Chunk` пропускаются. """
        skipped = {SOURCE_LABEL, CHUNK_LABEL, ENTITY_LABEL}
        with self._session() as session:
            for node_type in node_types:
                if not isinstance(node_type, dict):
                    continue
                label = node_type.get("type")
                unique_key = node_type.get("unique_key")
                if not isinstance(label, str) or not isinstance(unique_key, str):
                    continue
                if label in skipped:
                    continue
                _safe_type(label)
                _safe_type(unique_key)
                constraint_name = f"uniq_{label}_domain_{unique_key}"
                session.run(
                    f"CREATE CONSTRAINT {constraint_name} IF NOT EXISTS "
                    f"FOR (n:{label}) REQUIRE (n.domain, n.{unique_key}) IS UNIQUE"
                ).consume()

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
        depth = min(max(int(max_depth), 1), 3)
        walk = _expand_direction(direction)
        allowed = _expand_kinds(kinds)
        edge_types = f":{'|'.join(sorted(allowed))}" if allowed else ""
        span = f"{edge_types}*1..{depth}"
        pattern = f"-[{span}]->" if walk == "out" else f"<-[{span}]-" if walk == "in" else f"-[{span}]-"
        domain = ""
        for value in context_ids:
            if not isinstance(value, str) or not value.startswith("tag:"):
                continue
            parts = value.split(":")
            if len(parts) > 1 and parts[1]:
                domain = parts[1]
                break
        domain_clause = (
            " AND (seed.domain = $domain OR seed.node_id STARTS WITH $domain_prefix)"
            if domain
            else ""
        )
        path_domain_clause = (
            " WHERE all(item IN nodes(path) WHERE item.domain = $domain "
            "OR item.node_id STARTS WITH $domain_prefix)"
            if domain
            else ""
        )
        query = (
            "MATCH (seed) "
            "WHERE (seed.node_id IN $context_ids OR seed.tag_id IN $context_ids)"
            f"{domain_clause} "
            f"MATCH path=(seed){pattern}(node)"
            f"{path_domain_clause} "
            "RETURN node, [item IN nodes(path) | coalesce(item.node_id, item.tag_id)] AS path, "
            "length(path) AS depth, type(last(relationships(path))) AS kind "
            "LIMIT $max_nodes"
        )
        parameters: dict[str, Any] = {
            "context_ids": list(context_ids),
            "max_nodes": max(1, int(max_nodes)),
            "max_fanout": max(1, int(max_fanout)),
        }
        if domain:
            parameters["domain"] = domain
            parameters["domain_prefix"] = f"tag:{domain}:"
        with self._session() as session:
            rows = session.run(query, parameters=parameters).data()
        result: list[dict[str, Any]] = []
        fanout_counts: dict[str, int] = {}
        for row in rows:
            path = list(row.get("path") or [])
            seed = str(path[0]) if path else ""
            if fanout_counts.get(seed, 0) >= max_fanout:
                continue
            node = row.get("node")
            props = dict(node) if node is not None else {}
            result.append(
                {
                    "node_id": props.get("node_id") or props.get("tag_id"),
                    "canonical_name": props.get("canonical_name") or props.get("name"),
                    "path": list(row.get("path") or []),
                    "depth": int(row.get("depth") or 0),
                    "kind": row.get("kind") or "RELATED",
                    "source_ids": props.get("source_ids", []),
                    "chunk_ids": props.get("chunk_ids", []),
                    "domain": props.get("domain"),
                    "origin": props.get("origin"),
                    "confidence": props.get("confidence"),
                    "properties": dict(props.get("properties") or {}),
                }
            )
            fanout_counts[seed] = fanout_counts.get(seed, 0) + 1
            if len(result) >= max_nodes:
                break
        return result

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

    # ----------------------------------------------------------------- A-2 capability

    def consistency_capability(self) -> Consistency:
        """Neo4j-ось способна на атомарную запись пары при общем движке (ADR-024)."""
        return "atomic"

    def engine_key(self) -> str:
        return f"neo4j:{self._uri}:{self._database or ''}"

    # ----------------------------------------------------------------- ADR-028 transient

    def transient_aware(self) -> bool:
        """Neo4j — сетевое хранилище с deadlock/перезапусками: повторы допустимы."""
        return True

    def is_transient(self, exc: BaseException) -> bool:
        return _is_transient_exc(exc)

    @contextmanager
    def atomic_batch(self) -> Any:
        """Обе оси в одной транзакции Neo4j (A-2): один `session.begin_transaction()`.

        Объединяет операции графа и векторов (движок один), коммитит вместе; при
        исключении внутри блока транзакция откатывается целиком.
        """
        with self._session() as session, session.begin_transaction() as tx:
            yield _Neo4jBatch(tx)


class _Neo4jBatch:
    """Запись обеих осей в общей транзакции Neo4j (A-2, атомарная пара)."""

    def __init__(self, tx: Any) -> None:
        self._tx = tx

    def upsert_nodes(self, nodes: list[dict[str, Any]]) -> None:
        _upsert_nodes(self._tx, nodes)

    def upsert_edges(self, edges: list[dict[str, Any]]) -> None:
        _upsert_edges(self._tx, edges)

    def delete_node(self, node_id: str) -> bool:
        _delete_node(self._tx, node_id)
        return True

    def upsert_vectors(self, items: list[dict[str, Any]]) -> None:
        _upsert_vectors(self._tx, items)

    def delete_vectors(self, chunk_ids: list[str]) -> None:
        if chunk_ids:
            self._tx.run(
                f"MATCH (c:{CHUNK_LABEL}) WHERE c.node_id IN $ids DETACH DELETE c",
                parameters={"ids": chunk_ids},
            ).consume()

    def remove_source_from_entities(
        self,
        domain: str,
        source_url: str,
        chunk_ids: list[str],
    ) -> None:
        _remove_source(
            self._tx,
            {"domain": domain, "source_url": source_url, "chunk_ids": chunk_ids},
        )


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

    def remove_source_from_entities(
        self,
        domain: str,
        source_url: str,
        chunk_ids: list[str],
    ) -> None:
        self._buffered(
            "remove_source",
            {"domain": domain, "source_url": source_url, "chunk_ids": list(chunk_ids)},
        )

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
                elif kind == "remove_source":
                    _remove_source(tx, arg)
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


def _remove_source(runner: Any, values: dict[str, Any]) -> None:
    # `NOT value IN $list` в Neo4j 5 парсится как вычитание списков и падает
    # ("Cannot subtract `List` from `List`"), поэтому membership через ANY.
    runner.run(
        "MATCH (n) WHERE n.domain = $domain AND $source_url IN coalesce(n.source_ids, []) "
        "SET n.source_ids = [value IN coalesce(n.source_ids, []) WHERE value <> $source_url], "
        "n.chunk_ids = [value IN coalesce(n.chunk_ids, []) "
        "WHERE NOT any(c IN $chunk_ids WHERE c = value)]",
        parameters=values,
    ).consume()
    runner.run(
        "MATCH ()-[r]->() "
        "WHERE r.domain = $domain AND ("
        "$source_url IN coalesce(r.source_ids, []) "
        "OR any(value IN coalesce(r.chunk_ids, []) "
        "WHERE any(c IN $chunk_ids WHERE c = value))) "
        "SET r.source_ids = [value IN coalesce(r.source_ids, []) WHERE value <> $source_url], "
        "r.chunk_ids = [value IN coalesce(r.chunk_ids, []) "
        "WHERE NOT any(c IN $chunk_ids WHERE c = value)]",
        parameters=values,
    ).consume()


def _decode_properties(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return dict(decoded) if isinstance(decoded, dict) else {}
    return {}


def _encode_properties(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _encode_property_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _encode_properties(value)
    if isinstance(value, list):
        return [_encode_property_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _read_existing(runner: Any, query: str, parameters: dict[str, Any]) -> dict[str, Any]:
    rows = runner.run(query, parameters=parameters).data()
    return dict(rows[0]) if rows else {}


def _upsert_nodes(runner: Any, nodes: list[dict[str, Any]]) -> None:
    """Поштучный MERGE по node_id (ADR-028: сортировка входа — детерминированный
    порядок захвата замков, защита от deadlock-циклов)."""
    for node in sorted(nodes, key=lambda n: n["node_id"]):
        node_id = node["node_id"]
        for label in node.get("labels") or [ENTITY_LABEL]:
            _safe_type(label)
        labels = "".join(f":{_safe_type(l)}" for l in node.get("labels") or [ENTITY_LABEL])
        properties = dict(node.get("properties") or {})
        incoming_origin = str(properties.get("origin") or "")
        existing = _read_existing(
            runner,
            "MATCH (n {node_id: $node_id}) RETURN n.origin AS origin, n.properties AS properties",
            {"node_id": node_id},
        )
        existing_origin = str(existing.get("origin") or "")
        existing_nested = _decode_properties(existing.get("properties"))
        incoming_nested = _decode_properties(properties.get("properties"))
        preserve_manual = existing_origin == "user" and incoming_origin != "user"
        nested = existing_nested if preserve_manual else {**existing_nested, **incoming_nested}
        scalar_properties = {
            key: value
            for key, value in properties.items()
            if key not in _PROVENANCE_KEYS and key != "properties"
        }
        set_clauses = [
            (
                "SET n += CASE WHEN n.origin = 'user' AND $incoming_origin <> 'user' "
                "THEN {} ELSE $scalar_properties END"
            )
        ]
        if "properties" in properties or existing_nested:
            set_clauses.append("SET n.properties = $properties_value")
        for key in _PROVENANCE_KEYS:
            if isinstance(properties.get(key), list):
                # List comprehension вместо `new - old`: вычитание списков в
                # Cypher тип-строгое и падает на List[Any] (live Neo4j:
                # "Cannot subtract `List` from `List`").
                set_clauses.append(
                    f"SET n.{key} = coalesce(n.{key}, []) + "
                    f"[value IN $props.{key} WHERE NOT value IN coalesce(n.{key}, [])]"
                )
        parameters: dict[str, Any] = {
            "node_id": node_id,
            "props": properties,
            "scalar_properties": scalar_properties,
            "incoming_origin": incoming_origin,
        }
        if "properties" in properties or existing_nested:
            parameters["properties_value"] = _encode_properties(nested)
        runner.run(
            f"MERGE (n {{node_id: $node_id}}) SET n{labels} {' '.join(set_clauses)}",
            parameters=parameters,
        ).consume()


def _upsert_edges(runner: Any, edges: list[dict[str, Any]]) -> None:
    """Поштучный MERGE рёбер (ADR-028: сортировка по from_id, to_id, type)."""
    for edge in sorted(edges, key=lambda e: (e["from_id"], e["to_id"], e["type"])):
        rel_type = _safe_type(edge["type"])
        properties = dict(edge.get("properties") or {})
        incoming_origin = str(properties.get("origin") or "")
        existing = _read_existing(
            runner,
            f"MATCH (a {{node_id: $from}})-[r:{rel_type}]->(b {{node_id: $to}}) "
            "RETURN r.origin AS origin, r.properties AS properties",
            {"from": edge["from_id"], "to": edge["to_id"]},
        )
        existing_origin = str(existing.get("origin") or "")
        existing_nested = _decode_properties(existing.get("properties"))
        incoming_nested = _decode_properties(properties.get("properties"))
        preserve_manual = existing_origin == "user" and incoming_origin != "user"
        nested = existing_nested if preserve_manual else {**existing_nested, **incoming_nested}
        scalar_properties = {
            key: value
            for key, value in properties.items()
            if key not in _PROVENANCE_KEYS and key != "properties"
        }
        set_clauses = [
            (
                "SET r += CASE WHEN r.origin = 'user' AND $incoming_origin <> 'user' "
                "THEN {} ELSE $scalar_properties END"
            )
        ]
        if "properties" in properties or existing_nested:
            set_clauses.append("SET r.properties = $properties_value")
        for key in _PROVENANCE_KEYS:
            if isinstance(properties.get(key), list):
                set_clauses.append(
                    f"SET r.{key} = coalesce(r.{key}, []) + "
                    f"[value IN $props.{key} WHERE NOT value IN coalesce(r.{key}, [])]"
                )
        parameters: dict[str, Any] = {
            "from": edge["from_id"],
            "to": edge["to_id"],
            "type": edge["type"],
            "props": properties,
            "scalar_properties": scalar_properties,
            "incoming_origin": incoming_origin,
        }
        if "properties" in properties or existing_nested:
            parameters["properties_value"] = _encode_properties(nested)
        runner.run(
            f"MATCH (a {{node_id: $from}}) MATCH (b {{node_id: $to}}) "
            f"MERGE (a)-[r:{rel_type}]->(b) {' '.join(set_clauses)}",
            parameters=parameters,
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

    def update_vector_metadata(self, updates: list[dict[str, Any]]) -> int:
        updated_total = 0
        with self._session() as session:
            for update in updates:
                chunk_id = str(update["chunk_id"])
                raw = dict(update.get("metadata") or {})
                forbidden = set(raw) - VECTOR_METADATA_BACKFILL_KEYS
                if forbidden:
                    raise ValueError(f"запрещённые vector metadata fields: {sorted(forbidden)}")
                metadata = {
                    str(key): _encode_property_value(value)
                    for key, value in raw.items()
                }
                replace_lists = bool(update.get("replace", False))
                list_keys = {"context_ids", "tag_ids", "source_ids", "chunk_ids", "aliases"}
                scalar = {key: value for key, value in metadata.items() if key not in list_keys}
                clauses = ["SET c += $scalar_properties"] if scalar else []
                for key in list_keys:
                    if isinstance(metadata.get(key), list):
                        if replace_lists:
                            clauses.append(f"SET c.{key} = $props.{key}")
                        else:
                            # List comprehension вместо `$props.key - c.key`
                            # (live Neo4j: "Cannot subtract `List` from `List`").
                            clauses.append(
                                f"SET c.{key} = coalesce(c.{key}, []) + "
                                f"[value IN $props.{key} WHERE NOT value IN coalesce(c.{key}, [])]"
                            )
                where = "c.node_id = $chunk_id"
                parameters: dict[str, Any] = {
                    "chunk_id": chunk_id,
                    "props": metadata,
                    "scalar_properties": scalar,
                }
                if update.get("source_url") is not None:
                    where += " AND c.source_url = $source_url"
                    parameters["source_url"] = update["source_url"]
                if update.get("domain") is not None:
                    where += " AND c.domain = $domain"
                    parameters["domain"] = update["domain"]
                query = (
                    f"MATCH (c:{CHUNK_LABEL}) WHERE {where} "
                    f"{' '.join(clauses)} RETURN count(c) AS updated"
                )
                result = session.run(query, parameters=parameters).single()
                updated_total += int(result["updated"]) if result and result.get("updated") else 0
        return updated_total

    def get_vector_metadata(self, chunk_id: str) -> dict[str, Any] | None:
        with self._session() as session:
            rows = session.run(
                f"MATCH (c:{CHUNK_LABEL} {{node_id: $chunk_id}}) RETURN c",
                parameters={"chunk_id": chunk_id},
            ).data()
        if not rows:
            return None
        node = rows[0].get("c")
        if node is None:
            return None
        return dict(node)

    def verify_projection(self, domain: str, projection_revision: str) -> bool:
        with self._session() as session:
            row = session.run(
                f"MATCH (c:{CHUNK_LABEL}) WHERE c.domain = $domain "
                "AND (c.projection_revision IS NULL OR c.projection_revision <> $projection_revision) "
                "RETURN count(c) AS mismatches",
                parameters={"domain": domain, "projection_revision": projection_revision},
            ).single()
        return not row or int(row["mismatches"] or 0) == 0

    def list_chunk_ids_of_source(
        self,
        source_url: str,
        domain: str | None = None,
    ) -> list[str]:
        domain_clause = " AND c.domain = $domain" if domain is not None else ""
        parameters: dict[str, Any] = {"source_url": source_url}
        if domain is not None:
            parameters["domain"] = domain
        with self._session() as session:
            rows = session.run(
                f"MATCH (c:{CHUNK_LABEL}) WHERE c.source_url = $source_url{domain_clause} "
                "RETURN c.node_id AS chunk_id",
                parameters=parameters,
            ).data()
        return [str(row["chunk_id"]) for row in rows]

    def vector_search(
        self,
        embedding: list[float],
        top_k: int = 5,
        domain: str | None = None,
    ) -> list[dict[str, Any]]:
        domain_clause = " AND c.domain = $domain" if domain is not None else ""
        parameters = {"domain": domain} if domain is not None else {}
        with self._session() as session:
            rows = session.run(
                f"MATCH (c:{CHUNK_LABEL}) WHERE c.embedding IS NOT NULL{domain_clause} "
                "RETURN c.node_id AS chunk_id, c.embedding AS embedding, c",
                parameters=parameters,
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

    # ----------------------------------------------------------------- A-2 capability

    def consistency_capability(self) -> Consistency:
        """Neo4j-ось способна на атомарную запись пары при общем движке (ADR-024)."""
        return "atomic"

    def engine_key(self) -> str:
        return f"neo4j:{self._uri}:{self._database or ''}"

    # ----------------------------------------------------------------- ADR-028 transient

    def transient_aware(self) -> bool:
        """Neo4j — сетевое хранилище: повторы transient допустимы."""
        return True

    def is_transient(self, exc: BaseException) -> bool:
        return _is_transient_exc(exc)

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

    def vector_search(
        self,
        embedding: list[float],
        top_k: int = 5,
        domain: str | None = None,
    ) -> list[dict[str, Any]]:
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
        meta = {
            str(key): _encode_property_value(value)
            for key, value in (item.get("metadata") or {}).items()
        }
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