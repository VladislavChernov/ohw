"""Слой самовнимания (Self-Attention), реализованный с нуля."""
import torch
import torch.nn as nn
from config import DROPOUT_RATE


class SelfAttention(nn.Module):
    """
    Реализация механизма самовнимания (Self-Attention) с нуля.

    Каждый токен вычисляет свой контекст, взвешенно суммируя значения
    всех остальных токенов в последовательности (в пределах каузальной маски).

    Структура (Multi-Head):
        1. Линейные проекции Q, K, V и разделение на num_heads голов
        2. Scaled dot-product attention: Softmax(Q @ K^T / sqrt(d_head)) @ V
        3. Объединение голов и выходная линейная проекция
    """

    def __init__(self, d_model: int, num_heads: int = 8, dropout_rate: float = DROPOUT_RATE):
        super().__init__()
        assert d_model % num_heads == 0, "d_model должен делиться на num_heads"
        self.d_model = d_model
        self.num_heads = num_heads
        self.head_dim = d_model // num_heads

        # Линейные слои для Query (Q), Key (K) и Value (V)
        self.query = nn.Linear(d_model, d_model)
        self.key = nn.Linear(d_model, d_model)
        self.value = nn.Linear(d_model, d_model)
        self.out_proj = nn.Linear(d_model, d_model)
        self.dropout = nn.Dropout(p=dropout_rate)

    def forward(self, x: torch.Tensor, mask: torch.Tensor | None = None) -> torch.Tensor:
        """
        Вычисляет механизм внимания.

        Args:
            x: Входной тензор (B, L, D).
            mask: Каузальная маска (L, L) или (B, L, L), True = разрешено смотреть.
                  Если None, строится каузальная маска (нижний треугольник).

        Returns:
            Тензор выходных данных после Self-Attention (B, L, D).
        """
        batch, seq_len, _ = x.shape

        # 1. Линейные преобразования и разделение на головы
        Q = self.query(x).view(batch, seq_len, self.num_heads, self.head_dim).transpose(1, 2)
        K = self.key(x).view(batch, seq_len, self.num_heads, self.head_dim).transpose(1, 2)
        V = self.value(x).view(batch, seq_len, self.num_heads, self.head_dim).transpose(1, 2)

        # 2. Scaled dot-product attention: (B, H, L, L)
        attention_scores = torch.matmul(Q, K.transpose(-2, -1)) / (self.head_dim ** 0.5)

        # 3. Каузальная маска: токен на позиции i видит только позиции <= i
        if mask is None:
            mask = torch.tril(torch.ones((seq_len, seq_len), dtype=torch.bool, device=x.device))
        attention_scores = attention_scores.masked_fill(~mask, float("-inf"))

        # 4. Softmax и взвешенная сумма значений
        attention_weights = torch.softmax(attention_scores, dim=-1)
        attention_weights = self.dropout(attention_weights)
        context = torch.matmul(attention_weights, V)  # (B, H, L, d_head)

        # 5. Объединение голов и выходная проекция
        context = context.transpose(1, 2).contiguous().view(batch, seq_len, self.d_model)
        return self.out_proj(context)
