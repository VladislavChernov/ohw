"""
Модуль модели: Трансформер на один слой (Embedding + Self-Attention > Dense).

Архитектура простого LLM без позиционных эмбеддингов и FFN слоев:

  Token IDs > Embedding > [QKV] > Scaled Dot-Product Attention (Softmax) > Dense > Logits(vocab_size)

Здесь:
  - Embedding: преобразует токены в векторы фиксированной размерности
  - SelfAttention: кастомная реализация самовнимания (с каузальной маской)
  - Dense: предсказывает логиты для каждого токена в словаре (для классификации следующего токена)

Для предсказания следующего токена модель обучается в режиме teacher-forcing:
на каждой позиции последовательности предсказывается следующий токен.
"""

import torch
import torch.nn as nn
from config import DROPOUT_RATE
from src.layers import SelfAttention


class TransformerModel(nn.Module):
    """
    Простой трансформер с одним слоем: Embedding + Self-Attention > Dense.

    Архитектура модели (без позиционных эмбеддингов, без FFN слоев):

        Input (batch_size, seq_len) - токены
              |
              v
        Embedding > (batch_size, seq_len, d_model) - векторное представление токенов
              |
              v
        SelfAttention > (batch_size, seq_len, d_model) - контекст после внимания
              |
              v
        Dense layer > (batch_size, seq_len, vocab_size) - логиты для каждого токена
    """

    def __init__(self, vocab_size: int, embed_dim: int = 64, num_heads: int = 8, dropout: float = DROPOUT_RATE):
        """
        Инициализация модели.

        Args:
            vocab_size: Размер словаря (количество уникальных токенов)
            embed_dim: Размерность векторов эмбеддинга и скрытого представления
            num_heads: Число голов внимания в механизме self-attention
            dropout: Коэффициент dropout для регуляризации
        """
        super().__init__()
        self.vocab_size = vocab_size
        self.embed_dim = embed_dim
        self.num_heads = num_heads

        # 1. Эмбеддинг токенов (вход > векторное представление)
        self.embedding = nn.Embedding(vocab_size, embed_dim)

        # 2. Слой самовнимания (кастомная реализация, каузальная маска внутри)
        self.attention = SelfAttention(embed_dim, num_heads=num_heads, dropout_rate=dropout)

        # 3. Слой для предсказания следующего токена (выход > логиты словаря)
        self.dense = nn.Linear(embed_dim, vocab_size)

        print(f"Модель инициализирована: {vocab_size} токенов, d_model={embed_dim}, heads={num_heads}")

    def forward(self, x: torch.Tensor, mask: torch.Tensor | None = None):
        """
        Прямой проход (forward pass).

        Args:
            x: Входные данные - тензор токенов формы (batch_size, seq_len)
            mask: Каузальная маска (seq_len, seq_len), True = разрешено смотреть.
                  Если None - строится автоматически.

        Returns:
            Логиты для каждого токена словаря формы (batch_size, seq_len, vocab_size)
        """
        # 1. Эмбеддинг токенов > векторное представление
        embedded = self.embedding(x)  # (batch_size, seq_len, d_model)

        # 2. Self-attention: вычисление зависимостей между всеми позициями
        context = self.attention(embedded, mask)  # (batch_size, seq_len, d_model)

        # 3. Dense layer: предсказываем логиты для каждого токена словаря
        logits = self.dense(context)  # (batch_size, seq_len, vocab_size)

        return logits


if __name__ == "__main__":
    """Тест модели на простом примере."""
    from src.tokenizer import CharacterTokenizer

    tokenizer = CharacterTokenizer(max_vocab_size=256)

    with open("data/story.txt", "r", encoding="utf-8") as f:
        text = f.read()

    tokenizer.build_vocab(text)
    text_ids = tokenizer.encode_text(text)[:100]

    print(f"Токены: {text_ids[:20]}... (первые 20 из 100)")
    print(f"Размер словаря: {tokenizer.get_vocab_size()}")

    # Создаем батч (batch_size=1, seq_len=32) - для тестирования только структуры
    batch = torch.tensor(text_ids[:32], dtype=torch.long).unsqueeze(0)  # (1, 32)

    model = TransformerModel(vocab_size=tokenizer.get_vocab_size(), embed_dim=64, num_heads=8)

    print("\nТест forward pass...")
    logits = model(batch)
    print(f"   Вход: {batch.shape}")      # (1, 32)
    print(f"   Выход (логиты): {logits.shape}")  # (1, 32, vocab)

    print("\nМодель трансформера работает успешно!")