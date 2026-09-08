"""Context Assembly (docs/03_retriever.md §2, L3-03/L3-04, L1-05).

Детерминированная сборка промпта Python-кодом:
- графовый «скелет» вставляется первым и НЕ вытесняется (L3-03);
- жёсткий программный лимит токенов (L3-04) — по умолчанию 4096;
- при переполнении вытесняются только векторные чанки-«тело» с наименьшим
  reranker-score (вход отсортирован по убыванию скоров).
Оценка токенов — аппроксимация len(text.split()).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CONTEXT_TOKEN_LIMIT = 4096


def estimate_tokens(text: str) -> int:
    return len(text.split())


@dataclass
class ContextResult:
    text: str
    tokens: int
    skeleton_count: int
    body_count: int
    dropped: int


def render_skeleton(rows: list[dict[str, Any]]) -> list[str]:
    """Строки «скелета»: узел + связанный узел через тип рёбра."""
    parts: list[str] = []
    for row in rows:
        node = row.get("n") or {}
        neighbor = row.get("m") or {}
        rel_type = row.get("rel_type")
        label = _node_label(node)
        neighbor_label = _node_label(neighbor)
        if rel_type and neighbor:
            parts.append(f"{label} ~[{rel_type}]~ {neighbor_label}")
        else:
            parts.append(label)
    return parts


def _node_label(node: dict[str, Any]) -> str:
    for key in ("canonical_name", "name", "id", "node_id"):
        value = node.get(key)
        if isinstance(value, str) and value:
            return value
    node_id = node.get("_node_id")
    return str(node_id) if node_id else "?"


def render_body(chunk: dict[str, Any]) -> str:
    score = float(chunk.get("score") or 0.0)
    text = str(chunk.get("text") or "")
    source = str(chunk.get("source_url") or "")
    return f"[{score:.3f}] {source}: {text}"


class ContextAssembly:
    """Сборка {скелет, тело} в финальный промпт с детерминированным вытеснением."""

    def __init__(self, max_tokens: int = CONTEXT_TOKEN_LIMIT) -> None:
        self._max_tokens = max(max_tokens, 1)

    def assemble(self, skeleton_rows: list[dict[str, Any]], body_chunks: list[dict[str, Any]]) -> ContextResult:
        skeleton_parts = render_skeleton(skeleton_rows)
        skeleton_text = "\n".join(skeleton_parts)
        skeleton_tokens = estimate_tokens(skeleton_text)

        ordered = sorted(body_chunks, key=lambda c: float(c.get("score") or 0.0), reverse=True)
        body_parts: list[str] = []
        body_tokens = 0
        remaining = self._max_tokens - skeleton_tokens
        dropped = 0
        for chunk in ordered:
            rendered = render_body(chunk)
            tokens = estimate_tokens(rendered)
            if body_tokens + tokens > remaining:
                dropped += 1
                continue
            body_parts.append(rendered)
            body_tokens += tokens

        full = [skeleton_text] if skeleton_parts else []
        if body_parts:
            full.append("\n".join(body_parts))
        text = "\n\n".join(full)
        return ContextResult(
            text=text,
            tokens=skeleton_tokens + body_tokens,
            skeleton_count=len(skeleton_parts),
            body_count=len(body_parts),
            dropped=dropped,
        )