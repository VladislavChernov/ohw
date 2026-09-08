"""Нормализация строк Neo4j в плоские dict для ядра (L1-02: набор ядром интерфейсов).

Neo4j Node -> {"_node_id", "_labels", **props}; векторная строка -> metadata props.
Импорт neo4j-типов не требуется (duck-typing): контрактные тесты InMemory дают
аналогичные строки, обработка одинакова.
"""

from __future__ import annotations

from typing import Any


def _is_node(value: Any) -> bool:
    return (
        hasattr(value, "labels")
        and hasattr(value, "items")
        and hasattr(value, "get")
        and not isinstance(value, dict)
    )


def _plain_node(node: Any) -> dict[str, Any]:
    labels = list(node.labels)
    props = {key: node[key] for key in node}
    return {"_node_id": node.element_id if hasattr(node, "element_id") else props.get("node_id"), "_labels": labels, **props}


def normalize_graph_row(row: dict[str, Any]) -> dict[str, Any]:
    """Строка результата: значения-узлы превращаются в dict (иначе без изменений)."""
    out: dict[str, Any] = {}
    for key, value in row.items():
        out[key] = _plain_node(value) if _is_node(value) else value
    return out


def normalize_vector_row(row: dict[str, Any]) -> dict[str, Any]:
    """Строка vector_search: {chunk_id, score, text, source_url, domain, index}."""
    chunk_id = row.get("chunk_id")
    score = row.get("score", 0.0)
    node = row.get("c")
    props: dict[str, Any] = {}
    if isinstance(node, dict):
        props = {k: v for k, v in node.items() if not k.startswith("_")}
    result: dict[str, Any] = {
        "chunk_id": str(chunk_id),
        "score": round(float(score), 6),
    }
    for key in ("text", "source_url", "domain", "index"):
        if key in props:
            result[key] = props[key]
    return result