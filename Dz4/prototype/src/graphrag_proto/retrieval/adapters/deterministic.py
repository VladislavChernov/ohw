"""Детерминированные реализации без GPU (L4-01 в M2).

Единая функция `deterministic_embedding` — единственный источник векторов как на
этапе EMBED ингест-пайплайна, так и на эмбеддинге запроса: один и тот же текст
даёт один и тот же вектор (индекс/поиск согласованы).
"""

from __future__ import annotations

import hashlib

from graphrag_proto.retrieval.adapters.base import Embedder

DEFAULT_DIM = 8


def deterministic_embedding(text: str, dim: int = DEFAULT_DIM) -> list[float]:
    """Стабильный псевдо-вектор текста (детерминизм, без GPU/LLM)."""
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [float(b / 256.0) for b in digest[:dim]]


class DeterministicEmbedder(Embedder):
    """Эмбеддер M2: детерминированный хэш-вектор. Боевой bge-m3 — M3."""

    def __init__(self, dim: int = DEFAULT_DIM) -> None:
        self._dim = dim

    def embed(self, text: str, domain: str = "") -> list[float]:
        return deterministic_embedding(text, dim=self._dim)