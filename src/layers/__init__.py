"""Модуль слоев Transformer. Определяет строительные блоки LLM.

Реэкспорт публичных классов из подмодулей:
    - EmbeddingLayer (src/layers/embedding.py)
    - SelfAttention (src/layers/self_attention.py)
"""
from src.layers.embedding import EmbeddingLayer
from src.layers.self_attention import SelfAttention

__all__ = ["EmbeddingLayer", "SelfAttention"]
