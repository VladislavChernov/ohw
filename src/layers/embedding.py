"""Слой эмбеддинга: преобразует индексы токенов в плотные векторы."""
import torch
import torch.nn as nn


class EmbeddingLayer(nn.Module):
    """Слой эмбеддинга: преобразует индексы токенов в плотные векторы."""

    def __init__(self, vocab_size: int, embed_dim: int):
        super().__init__()
        self.embedding = nn.Embedding(vocab_size, embed_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.embedding(x)
