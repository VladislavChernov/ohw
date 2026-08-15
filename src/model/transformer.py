"""
Модуль модели: Трансформер на один слой (Embedding + Self-Attention → Dense).

Архитектура простого LLM без позиционных эмбеддингов и FFN слоев:

  Token IDs → EmbeddingLayer → [QKV] → Scaled Dot-Product Attention → Softmax → Dense → Logits(vocab_size)

Здесь:
  - Embedding: преобразует токены в векторы фиксированной размерности
  - Self-Attention: вычисляет зависимости между всеми токенами в последовательности
  - Dense: предсказывает логиты для каждого токена в словаре (для классификации следующего токена)

Это минимальная модель на основе статьи Рашка "LLMs from Scratch" — только один слой, без позиционных эмбеддингов.
"""

import torch
import torch.nn as nn


class TransformerModel(nn.Module):
    """
    Простой трансформер с одним слоем: Embedding + Self-Attention → Dense.
    
    Архитектура модели (без позиционных эмбеддингов, без FFN слоев):
    
        Input (batch_size, seq_len) — токены
              │
              ▼
        EmbeddingLayer → (batch_size, seq_len, d_model) — векторное представление токенов
              │
              ▼
      Self-Attention → (batch_size, seq_len, d_model) — после механизма внимания
              │
              ▼
       Dense layer → (batch_size, vocab_size) — логиты для каждого токена словаря
    
    Для предсказания следующего токена используется только последний элемент 
    последовательности (или можно использовать весь контекст с masking).
    """

    def __init__(self, vocab_size: int, embed_dim: int = 64, num_heads: int = 8, dropout: float = 0.1):
        """
        Инициализация модели.
        
        Args:
            vocab_size: Размер словаря (количество уникальных токенов)
            embed_dim: Размерность векторов эмбеддинга и скрытого представления
            num_heads: Число голов внимания в механизме self-attention
            dropout: Коэффициент dropout для регуляризации
        """
        super().__init__()
        
        # 1. Эмбеддинг токенов (вход → векторное представление)
        self.embedding = nn.Embedding(vocab_size, embed_dim, padding_idx=0)
        
        # 2. Самой слой внимания (без позиционных эмбеддингов!)
        # QKV-проекция + scaled dot-product attention
        self.attention = nn.MultiHeadAttention(
            embed_dim=embed_dim,
            num_heads=num_heads,
            batch_first=True  # (batch_size, seq_len, d_model)
        )
        
        # 3. Слой для предсказания следующего токена (выход → логиты словаря)
        self.dense = nn.Linear(embed_dim, vocab_size)
        
        print(f"🧠 Модель инициализирована: {vocab_size} токенов, d_model={embed_dim}, heads={num_heads}")

    def forward(self, x: torch.Tensor):
        """
        Прямой проход (forward pass).
        
        Args:
            x: Входные данные — тензор токенов формы (batch_size, seq_len)
            
        Returns:
            Logits для каждого токена словаря формы (batch_size, vocab_size)
        """
        # 1. Эмбеддинг токенов → векторное представление
        embedded = self.embedding(x)  # (batch_size, seq_len, d_model)
        
        # 2. Self-attention: вычисление зависимостей между всеми позициями
        # Для простоты модели — без positional encoding, только raw embeddings
        context = self.attention(
            query=embedded,
            key=embedded,
            value=embedded
        )  # (batch_size, seq_len, d_model)
        
        # 3. Dense layer: предсказываем логиты для каждого токена словаря
        # Применяем к каждому токену в последовательности (для предсказания следующего)
        logits = self.dense(context)  # (batch_size, seq_len, vocab_size)
        
        return logits

    def predict_next(self, x: torch.Tensor, vocab_size: int):
        """
        Предсказывает следующий токен на основе текущего контекста.
        
        Args:
            x: Вектор эмбеддинга последнего токена (d_model,)
            vocab_size: Размер словаря
            
        Returns:
            Индекс наиболее вероятного следующего токена
        """
        # Добавляем батч- размерность, если нужно
        if x.dim() == 1:
            x = x.unsqueeze(0)
        
        # Используем только последний токен контекста (или средний по всем позициям)
        context = self.attention(
            query=x, key=x, value=x
        )
        
        logits = self.dense(context.squeeze(0))  # (vocab_size,)
        
        # Возвращаем индекс наиболее вероятного токена (argmax)
        return torch.argmax(logits).item()


if __name__ == "__main__":
    """Тест модели на простом примере."""
    import os
    
    from src.tokenizer import CharacterTokenizer
    
    # Инициализация токенизатора и загрузка данных
    tokenizer = CharacterTokenizer(vocab_size=256)
    
    with open("data/story.txt", "r", encoding="utf-8") as f:
        text = f.read()
    
    # Токенизация текста
    text_ids = tokenizer.encode_text(text)[:100]  # Берем первые 100 токенов для тестирования
    
    print(f"📊 Токены: {text_ids[:20]}... (первые 20 из 100)")
    print(f"   Размер словаря: {tokenizer.get_vocab_size()}")
    
    # Создаем батч (batch_size=4, seq_len=32) — для тестирования только структуры
    batch = torch.tensor(text_ids[:32], dtype=torch.long).unsqueeze(0)  # (1, 32)
    
    # Инициализация модели
    model = TransformerModel(vocab_size=256, embed_dim=64, num_heads=8)
    
    print(f"\n🧪 Тест forward pass...")
    logits = model(batch)
    print(f"   Вход: {batch.shape}")  # (1, 32)
    print(f"   Выход (логиты): {logits.shape}")  # (1, 32, 256)
    
    print("\n✅ Модель трансформера работала успешно!")