"""Модуль слоев Transformer. Определяет строительные блоки LLM."""
import torch.nn as nn
import torch
from config import EMBEDDING_DIM, DROPOUT_RATE # Импорт констант

class EmbeddingLayer(nn.Module):
    # Контракт для класса, который будет использовать nn.Embedding(...)
    def __init__(self, vocab_size: int, embed_dim: int):
        super().__init__()
        self.embedding = nn.Embedding(vocab_size, embed_dim)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.embedding(x)


class SelfAttention(nn.Module):
    """
    Реализация механизма самовнимания (Self-Attention).
    Это упрощенная версия Multi-Head Attention для учебных целей (Single Head).

    Внимание позволяет каждому токену вычислить свой контекст, взвешенно
    суммируя значения всех остальных токенов в последовательности.
    """
    def __init__(self, d_model: int, dropout_rate: float = DROPOUT_RATE):
        super().__init__()
        # Инициализация линейных слоев для Query (Q), Key (K) и Value (V)
        self.query = nn.Linear(d_model, d_model)
        self.key = nn.Linear(d_model, d_model)
        self.value = nn.Linear(d_model, d_model)
        self.dropout = nn.Dropout(p=DROPOUT_RATE)

    def forward(self, x: torch.Tensor, mask: torch.Tensor = None) -> torch.Tensor:
        """
        Вычисляет механизму внимания.
        
        Args:
            x: Входной тензор (B, L, D).
            mask: Маска для пропуска токенов (например, каузальная маска).
        Returns:
            Тензор выходных данных после Self-Attention (B, L, D).
        """
        # 1. Линейные преобразования для Q, K, V
        Q = self.query(x)
        K = self.key(x)
        V = self.value(x)
        
        # 2. Расчет внимания: Softmax((Q * K^T / sqrt(d_k)) * V)
        # (B, L, D) @ (B, D, L) -> (B, L, L)
        attention_scores = torch.matmul(Q, K.transpose(-2, -1)) / (torch.sqrt(torch.tensor(Q.size(-1), dtype=torch.float32)))

        # 3. Применение маски (если есть)
        if mask is not None:
            attention_scores = attention_scores.masked_fill(mask == 0, float('-inf'))

        # 4. Softmax и взвешенная сумма
        attention_weights = torch.softmax(attention_scores, dim=-1)
        output = self.dropout(torch.matmul(attention_weights, V))
        
        return output