"""Context Assembly (docs/03_retriever.md §2, L3-03/L3-04, L1-05).

Детерминированная сборка промпта Python-кодом:
- графовый «скелет» вставляется первым и НЕ вытесняется (L3-03);
- жёсткий программный лимит токенов (L3-04) — по умолчанию 4096;
- при переполнении вытесняются только векторные чанки-«тело» с наименьшим
  reranker-score (вход отсортирован по убыванию скоров).
Оценка токенов — аппроксимация len(text.split()).
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    # Токены скелета и тела - РАЗДЕЛЬНО, а не только их сумму. Пока известна лишь сумма,
    # нельзя отличить "граф занял место текста" от "граф занял своё место": скелет входит
    # первым и не вытесняется, тело получает остаток, поэтому доля скелета - это и есть
    # цена, которую платит тело.
    skeleton_tokens: int = 0
    body_tokens: int = 0
    max_tokens: int = 0
    # САМИ строки, попавшие в промпт. Без них вопрос "что именно модель увидела от графа"
    # по готовому прогону не отвечается вовсе: числа говорят, сколько строк было, а что
    # они содержали - остаётся догадкой. Объём ограничен сверху `max_graph_nodes` и бюджетом,
    # то есть сохраняемое не превосходит того, что реально ушло в промпт.
    skeleton_lines: list[str] = field(default_factory=list)
    # Идентификаторы чанков, чей текст РЕАЛЬНО вошёл в промпт (не вытеснен лимитом).
    # Критерий ADR-072 требует «текст чанка доехал в контекст», и по `body_count` этого
    # не видно: число говорит, сколько чанков было, но не какие. Чанк без `chunk_id`
    # в список не попадает — «не измерено» не превращается в пустую строку-идентификатор.
    body_chunk_ids: list[str] = field(default_factory=list)


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
        skeleton_budget = max(1, self._max_tokens // 2)
        selected_skeleton: list[str] = []
        skeleton_tokens = 0
        dropped = 0
        for part in skeleton_parts:
            tokens = estimate_tokens(part)
            if skeleton_tokens + tokens <= skeleton_budget:
                selected_skeleton.append(part)
                skeleton_tokens += tokens
                continue
            available = skeleton_budget - skeleton_tokens
            if available > 0:
                selected_skeleton.append(" ".join(part.split()[:available]))
                skeleton_tokens += available
            dropped += 1
        skeleton_text = "\n".join(selected_skeleton)

        ordered = sorted(body_chunks, key=lambda c: float(c.get("score") or 0.0), reverse=True)
        body_parts: list[str] = []
        body_chunk_ids: list[str] = []
        body_tokens = 0
        remaining = self._max_tokens - skeleton_tokens
        for chunk in ordered:
            rendered = render_body(chunk)
            tokens = estimate_tokens(rendered)
            if body_tokens + tokens > remaining:
                dropped += 1
                continue
            body_parts.append(rendered)
            body_tokens += tokens
            chunk_id = str(chunk.get("chunk_id") or "")
            if chunk_id:
                body_chunk_ids.append(chunk_id)

        full = [skeleton_text] if selected_skeleton else []
        if body_parts:
            full.append("\n".join(body_parts))
        text = "\n\n".join(full)
        return ContextResult(
            text=text,
            tokens=skeleton_tokens + body_tokens,
            skeleton_count=len(selected_skeleton),
            body_count=len(body_parts),
            dropped=dropped,
            skeleton_tokens=skeleton_tokens,
            body_tokens=body_tokens,
            max_tokens=self._max_tokens,
            skeleton_lines=list(selected_skeleton),
            body_chunk_ids=body_chunk_ids,
        )