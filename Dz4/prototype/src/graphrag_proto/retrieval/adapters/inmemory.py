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
from collections.abc import Sequence
from contextlib import contextmanager
from typing import Any

from graphrag_proto.retrieval.adapters.base import (
    OWNERLESS_SOURCE,
    VECTOR_METADATA_BACKFILL_KEYS,
    Consistency,
    GraphStoreProvider,
    VectorStoreProvider,
    _expand_depth,
    _expand_direction,
    _expand_kinds,
)

_TERM_COND_RE = re.compile(r"toLower\(n\.(\w+)\)\s+CONTAINS\s+toLower\(t\)")
_LABEL_COND_RE = re.compile(r"m:(\w+)")
_PROVENANCE_KEYS = ("source_ids", "chunk_ids", "variants", "aliases")



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
                node_id = node["node_id"]
                existing = self._nodes.get(node_id)
                incoming_properties = dict(node.get("properties") or {})
                if existing is None:
                    self._nodes[node_id] = {
                        "labels": list(node.get("labels") or []),
                        "properties": incoming_properties,
                    }
                    continue
                properties = dict(existing["properties"])
                preserve_manual = (
                    str(properties.get("origin")) == "user"
                    and str(incoming_properties.get("origin")) != "user"
                )
                for key, value in incoming_properties.items():
                    if preserve_manual and key not in _PROVENANCE_KEYS:
                        continue
                    if key in _PROVENANCE_KEYS and isinstance(properties.get(key), list) and isinstance(value, list):
                        properties[key] = list(dict.fromkeys([*properties[key], *value]))
                    elif key == "properties" and isinstance(properties.get(key), dict) and isinstance(value, dict):
                        properties[key] = {**properties[key], **value}
                    elif key == "origin" and str(properties.get(key)) == "user" and str(value) == "ai":
                        continue
                    else:
                        properties[key] = value
                self._nodes[node_id] = {
                    "labels": list(dict.fromkeys([*existing["labels"], *(node.get("labels") or [])])),
                    "properties": properties,
                }
        elif kind == "delete_edge":
            from_id, to_id, edge_type = arg
            self._edges.pop((from_id, to_id, edge_type), None)
        elif kind == "remove_source":
            domain = arg["domain"]
            source_url = arg["source_url"]
            chunk_ids = set(arg["chunk_ids"])
            for node in self._nodes.values():
                properties = node["properties"]
                if properties.get("domain") != domain:
                    continue
                source_values = properties.get("source_ids")
                chunk_values = properties.get("chunk_ids")
                source_matches = isinstance(source_values, list) and source_url in source_values
                chunk_matches = isinstance(chunk_values, list) and bool(chunk_ids.intersection(chunk_values))
                if not source_matches and not chunk_matches:
                    continue
                if isinstance(source_values, list):
                    properties["source_ids"] = [value for value in source_values if value != source_url]
                # Только если свойство уже было: создание `chunk_ids = []` превращало
                # нетронутую структурную сущность в кандидата на уборку, то есть обновление
                # документа само порождало мусор, который затем съедала уборка.
                if isinstance(chunk_values, list):
                    properties["chunk_ids"] = [
                        value for value in chunk_values if value not in chunk_ids
                    ]
            for _key, properties in list(self._edges.items()):
                edge_domain = properties.get("domain")
                if edge_domain is not None and edge_domain != domain:
                    continue
                source_values = properties.get("source_ids")
                chunk_values = properties.get("chunk_ids")
                source_matches = isinstance(source_values, list) and source_url in source_values
                chunk_matches = isinstance(chunk_values, list) and bool(chunk_ids.intersection(chunk_values))
                if not source_matches and not chunk_matches:
                    continue
                if isinstance(source_values, list):
                    properties["source_ids"] = [
                        value for value in source_values if value != source_url
                    ]
                # Только если свойство уже было: создание `chunk_ids = []` превращало
                # нетронутую структурную связь в кандидата на уборку, то есть обновление
                # документа само порождало мусор, который затем съедала уборка.
                if isinstance(chunk_values, list):
                    properties["chunk_ids"] = [
                        value for value in chunk_values if value not in chunk_ids
                    ]
        elif kind == "upsert_edges":
            for edge in arg:
                key = (edge["from_id"], edge["to_id"], edge["type"])
                incoming = dict(edge.get("properties") or {})
                existing = self._edges.get(key, {})
                merged = dict(existing)
                preserve_manual = (
                    str(existing.get("origin")) == "user"
                    and str(incoming.get("origin")) != "user"
                )
                for name, value in incoming.items():
                    if preserve_manual and name not in {"source_ids", "aliases"}:
                        continue
                    if name in {"source_ids", "aliases"} and isinstance(value, list):
                        merged[name] = list(dict.fromkeys([*existing.get(name, []), *value]))
                    elif name == "properties" and isinstance(value, dict):
                        merged[name] = {**dict(existing.get(name) or {}), **value}
                    elif name == "origin" and existing.get(name) == "user" and value == "ai":
                        continue
                    else:
                        merged[name] = value
                self._edges[key] = merged
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
        domain = params.get("domain")

        rows: list[dict[str, Any]] = []
        for node_id, node in sorted(self._nodes.items()):
            if domain is not None and node["properties"].get("domain") != domain:
                continue
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
        walk = _expand_direction(direction)
        allowed = _expand_kinds(kinds)
        depth_limit = _expand_depth(max_depth)
        by_id = {node_id: node for node_id, node in self._nodes.items()}
        by_tag = {
            str(node["properties"].get("tag_id")): node_id
            for node_id, node in self._nodes.items()
            if node["properties"].get("tag_id")
        }
        seeds: list[str] = []
        for context_id in context_ids:
            node_id = context_id if context_id in by_id else by_tag.get(context_id)
            if not node_id:
                continue
            node_domain = by_id[node_id]["properties"].get("domain")
            if node_domain and context_id.startswith("tag:"):
                parts = context_id.split(":")
                if len(parts) > 1 and parts[1] and parts[1] != str(node_domain):
                    continue
            if node_id not in seeds:
                seeds.append(node_id)
        queue: list[tuple[str, int, list[str]]] = [(seed, 0, [seed]) for seed in seeds]
        visited = set(seeds)
        result: list[dict[str, Any]] = []
        while queue and len(result) < max_nodes:
            current, depth, path = queue.pop(0)
            if depth >= depth_limit:
                continue
            fanout = 0
            for (from_id, to_id, edge_type), properties in self._edges.items():
                kind = str(properties.get("kind") or edge_type)
                if allowed is not None and kind.upper() not in allowed:
                    continue
                if walk == "out":
                    if from_id != current:
                        continue
                    neighbor = to_id
                elif walk == "in":
                    if to_id != current:
                        continue
                    neighbor = from_id
                else:
                    if current == from_id:
                        neighbor = to_id
                    elif current == to_id:
                        neighbor = from_id
                    else:
                        continue
                if neighbor in visited or fanout >= max_fanout:
                    continue
                node = by_id.get(neighbor)
                if node is None:
                    continue
                current_domain = by_id[current]["properties"].get("domain")
                node_domain = node["properties"].get("domain")
                if current_domain and node_domain and current_domain != node_domain:
                    continue
                visited.add(neighbor)
                fanout += 1
                next_path = [*path, neighbor]
                result.append(
                    {
                        "node_id": neighbor,
                        "canonical_name": node["properties"].get("canonical_name")
                        or node["properties"].get("name")
                        or node["properties"].get("tag_id"),
                        "path": next_path,
                        "depth": depth + 1,
                        "kind": kind,
                        "source_ids": node["properties"].get("source_ids", []),
                        "chunk_ids": node["properties"].get("chunk_ids", []),
                        "domain": node["properties"].get("domain"),
                        "origin": node["properties"].get("origin"),
                        "confidence": node["properties"].get("confidence"),
                        "properties": dict(node["properties"].get("properties") or {}),
                    }
                )
                queue.append((neighbor, depth + 1, next_path))
                if len(result) >= max_nodes:
                    break
        return result

    def upsert_nodes(self, nodes: list[dict[str, Any]]) -> None:
        self._mutate("upsert_nodes", nodes)

    def upsert_edges(self, edges: list[dict[str, Any]]) -> None:
        self._mutate("upsert_edges", edges)

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        node = self._nodes.get(node_id)
        return self._node_view(node_id) if node else None

    def verify_edge(self, from_id: str, to_id: str, edge_type: str) -> bool:
        return (from_id, to_id, edge_type) in self._edges

    def delete_node(self, node_id: str) -> bool:
        if self._nodes.get(node_id) is None:
            return self._journal is not None
        self._mutate("delete_node", node_id)
        return True

    def remove_source_from_entities(
        self,
        domain: str,
        source_url: str,
        chunk_ids: list[str],
    ) -> None:
        self._mutate(
            "remove_source",
            {"domain": domain, "source_url": source_url, "chunk_ids": list(chunk_ids)},
        )

    def delete_orphans(self, domain: str, *, dry_run: bool) -> tuple[int, int]:
        """Осиротевшие доменные связи и узлы (ADR-014, `docs/02` §4.5).

        Три правила зафиксированы до кода и здесь соблюдаются буквально:

        - канонический признак жизни — **непустой `chunk_ids`**, а не `source_ids`; наличие
          свойства обязательно (`is not None`), иначе под нож попадут структурные рёбра, у
          которых `chunk_ids` нет вовсе;
        - **порядок: связи, затем узлы** — обратный ломает счёт, удалённый узел уносит связь,
          которую мы посчитали удалённой, не удаляя её;
        - **`dry_run` ничего не удаляет**, только считает.

        Узлы считаются мусорными, если у них нет ни поддерживающих чанков, ни инцидентных
        связей, и удаляются с инцидентными рёбрами (в Neo4j это `DETACH DELETE`).
        """
        orphan_edges: list[tuple[str, str, str]] = []
        for key, properties in self._edges.items():
            if "chunk_ids" not in properties:
                continue  # структурное ребро: свойства нет, а значит нет и основания судить
            if properties.get("domain") != domain:
                continue
            chunk_values = properties.get("chunk_ids")
            if isinstance(chunk_values, list) and chunk_values:
                continue  # поддержано живым чанком
            orphan_edges.append(key)

        orphan_nodes = [
            node_id
            for node_id, node in self._nodes.items()
            if node["properties"].get("domain") == domain
            and isinstance(node["properties"].get("chunk_ids"), list)
            and not node["properties"]["chunk_ids"]
            and not any(
                from_id == node_id or to_id == node_id for from_id, to_id, _etype in self._edges
            )
        ]

        # ADR-047: счётчики раздельные. Сумма попадала в поле связей, и удалённые узлы
        # отчётом назывались связями.
        planned = (len(orphan_edges), len(orphan_nodes))
        if dry_run:
            return planned

        # Внутри транзакции уборка не откатывается: подсчёт читает текущее состояние, а
        # буфер применяется лишь на выходе, поэтому вернувшийся счётчик не соответствовал бы
        # удалённому. Раньше это дало бы «правдоподобный ноль» в отчёте. Запрещаем явно,
        # а не молча, и по той же причине удаление не идёт в горячий путь (ADR-028):
        # откатить удалённое без повторной экстракции нельзя.
        if self._journal is not None:
            raise RuntimeError(
                "delete_orphans нельзя вызывать внутри write-транзакции: "
                "подсчёт до удаления обязателен, а буфер применяется на выходе"
            )
        for key in orphan_edges:
            self._do("delete_edge", list(key))
        for node_id in orphan_nodes:
            self._do("delete_node", node_id)
        return planned

    def chunk_revisions_by_source(self, domain: str) -> dict[str, dict[str, int]] | None:
        """Ревизии чанков по владельцу, один проход по нодам (ADR-046 п. 3)."""
        grouped: dict[str, dict[str, int]] = {}
        for node in self._nodes.values():
            if "Chunk" not in list(node.get("labels") or []):
                continue
            properties = node.get("properties") or {}
            if str(properties.get("domain") or "") != domain:
                continue
            owner = str(properties.get("source_url") or "") or OWNERLESS_SOURCE
            revisions = grouped.setdefault(owner, {})
            revision = str(properties.get("projection_revision") or "")
            revisions[revision] = revisions.get(revision, 0) + 1
        return grouped

    def list_chunk_ids_of_source(self, source_url: str, domain: str) -> list[str]:
        """Чанки документа по полю владельца (ADR-046 п. 9: якорь `Source` удалён, раньше
        здесь был обход `CONTAINS`)."""
        return sorted(
            node_id
            for node_id, node in self._nodes.items()
            if "Chunk" in list(node.get("labels") or [])
            and str((node.get("properties") or {}).get("source_url") or "") == source_url
            and str((node.get("properties") or {}).get("domain") or "") == domain
        )

    # ----------------------------------------------------------------- A-2 capability

    def consistency_capability(self) -> Consistency:
        return "atomic"

    def engine_key(self) -> str:
        return "inmemory://local"

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

    def _do(self, kind: str, arg: Any) -> int | None:
        if kind == "upsert_vectors":
            for item in arg:
                self._vectors[item["chunk_id"]] = item
        elif kind == "delete_vectors":
            for chunk_id in arg:
                self._vectors.pop(chunk_id, None)
        elif kind == "update_vector_metadata":
            updated = 0
            for update in arg:
                chunk_id = str(update["chunk_id"])
                current = self._vectors.get(chunk_id)
                if current is None:
                    raise KeyError(f"vector record не найден: {chunk_id}")
                metadata = dict(current.get("metadata") or {})
                patch = dict(update.get("metadata") or {})
                replace_lists = bool(update.get("replace", False))
                forbidden = set(patch) - VECTOR_METADATA_BACKFILL_KEYS
                if forbidden:
                    raise ValueError(f"запрещённые vector metadata fields: {sorted(forbidden)}")
                expected_source = update.get("source_url")
                expected_domain = update.get("domain")
                if expected_source is not None and metadata.get("source_url") != expected_source:
                    raise ValueError(f"vector record {chunk_id} принадлежит другому source")
                if expected_domain is not None and metadata.get("domain") != expected_domain:
                    raise ValueError(f"vector record {chunk_id} принадлежит другому domain")
                for key, value in patch.items():
                    if replace_lists and isinstance(value, list):
                        metadata[key] = list(dict.fromkeys(value))
                    elif isinstance(value, list) and isinstance(metadata.get(key), list):
                        metadata[key] = list(dict.fromkeys([*metadata[key], *value]))
                    elif isinstance(value, dict) and isinstance(metadata.get(key), dict):
                        metadata[key] = {**metadata[key], **value}
                    else:
                        metadata[key] = value
                self._vectors[chunk_id] = {**current, "metadata": metadata}
                updated += 1
            return updated
        else:
            raise NotImplementedError(f"неизвестная операция: {kind!r}")
        return None

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

    def update_vector_metadata(self, updates: list[dict[str, Any]]) -> int:
        if self._journal is not None:
            self._journal.record("update_vector_metadata", updates)
            return len(updates)
        return int(self._do("update_vector_metadata", updates) or 0)

    def get_vector_metadata(self, chunk_id: str) -> dict[str, Any] | None:
        item = self._vectors.get(chunk_id)
        if item is None:
            return None
        return dict(item.get("metadata") or {})

    def verify_projection(self, domain: str, projection_revision: str) -> bool:
        return all(
            str((item.get("metadata") or {}).get("domain")) != domain
            or str((item.get("metadata") or {}).get("projection_revision")) == projection_revision
            for item in self._vectors.values()
        )

    def list_chunk_ids_of_source(
        self,
        source_url: str,
        domain: str | None = None,
    ) -> list[str]:
        return sorted(
            chunk_id
            for chunk_id, item in self._vectors.items()
            if (item.get("metadata") or {}).get("source_url") == source_url
            and (domain is None or (item.get("metadata") or {}).get("domain") == domain)
        )

    def vector_search(
        self,
        embedding: list[float],
        top_k: int = 5,
        domain: str | None = None,
    ) -> list[dict[str, Any]]:
        scored: list[tuple[float, str]] = []
        for chunk_id, item in self._vectors.items():
            metadata = item.get("metadata") or {}
            if domain is not None and metadata.get("domain") != domain:
                continue
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

    # ----------------------------------------------------------------- A-2 capability

    def consistency_capability(self) -> Consistency:
        return "atomic"

    def engine_key(self) -> str:
        return "inmemory://local"
