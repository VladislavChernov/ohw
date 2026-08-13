"""
Модуль обучения: Цикл обучения LLM на 15 эпох.

Требования к обучению (по книге Рашка "LLMs from Scratch"):
- Модель: простой трансформер на один слой (Embedding + Self-Attention → Dense)
- Данные: ТОЛЬКО data/story.txt (единственный источник данных)
- Целевая функция потерь: CrossEntropyLoss
- Оптимизатор: Adam с learning_rate = 0.001
- Эпох: 15
- На каждой эпохе выводить значение loss

Процесс обучения:
1. Загрузка story.txt и токенизация
2. Создание DataLoader с батчами (seq_len-1, next_token)
3. Инициализация модели (Embedding + Self-Attention → Dense)
4. Обучение на 15 эпох с CrossEntropyLoss + Adam optimizer
5. Вывод loss на каждой эпохе
"""

import torch
import torch.nn as nn
from torch.utils.data import DataLoader
from src.tokenizer import CharacterTokenizer
from src.data.dataloader import get_data_loader
from src.model.transformer import TransformerModel


def train_model(epochs: int = 15, batch_size: int = 32, seq_len: int = 64):
    """
    Функция обучения модели на данных из story.txt.
    
    Args:
        epochs: Количество эпох обучения (по умолчанию 15)
        batch_size: Размер батча (по умолчанию 32)
        seq_len: Длина последовательности, включая целевой токен (64)
        
    Процесс:
    1. Токенизация story.txt → получаем словарь токенов
    2. Создание DataLoader с парами (input: seq_len-1, target: next_token)
    3. Инициализация модели TransformerModel (Embedding + Self-Attention → Dense)
    4. Цикл обучения на `epochs` итераций:
       а. Для каждого батча: forward pass → предсказание логитов
       б. Вычисление потерь через CrossEntropyLoss
       в. Backpropagation (backward + step)
       г. Вывод значения loss на текущей эпохе
    """
    
    print("=" * 60)
    print("🚀 НАЧАЛИЕ ОБУЧЕНИЯ LLM — 15 ЭПОХ")
    print(f"   Модель: TransformerModel (Embedding + Self-Attention → Dense)")
    print(f"   Данные: data/story.txt (единственный источник)")
    print(f"   Seq len: {seq_len} | Batch size: {batch_size}")
    print("=" * 60)

    # --- 1. Токенизация данных ---
    tokenizer = CharacterTokenizer(vocab_size=256)
    
    with open("data/story.txt", "r", encoding="utf-8") as f:
        text = f.read()
    
    print(f"\n📄 Загружено {len(text)} символов из story.txt")
    
    # --- 2. Создание DataLoader ---
    dataloader = get_data_loader(
        text=text, 
        tokenizer=tokenizer, 
        seq_len=seq_len,
        batch_size=batch_size
    )
    
    print(f"\n📦 Датасет готов: {len(dataloader.dataset)} примеров")

    # --- 3. Инициализация модели ---
    model = TransformerModel(
        vocab_size=tokenizer.get_vocab_size(), 
        embed_dim=64, 
        num_heads=8
    )
    
    # Выбор устройства (GPU если доступно)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"\n🖥️  Работаем на устройстве: {device}")

    # --- 4. Настройка функции потерь и оптимизатора ---
    criterion = nn.CrossEntropyLoss()  # cross-entropy loss для классификации токенов
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)  # Adam с lr=0.001

    model.to(device)

    # --- 5. Цикл обучения на 15 эпох ---
    print(f"\n📈 Начинаем обучение: {epochs} эпох\n")
    
    for epoch in range(1, epochs + 1):
        # Находим минимальный loss в текущей эпохе (для отладки)
        min_epoch_loss = float('inf')
        
        # Инициализация для накопления потерь
        total_loss = 0.0
        
        for batch_idx, (inputs, targets) in enumerate(dataloader):
            inputs = inputs.to(device)      # (batch_size, seq_len-1)
            targets = targets.to(device)    # (batch_size, 1)
            
            # Forward pass
            logits = model(inputs)          # (batch_size, seq_len-1, vocab_size)
            
            # Перемещаем логиты и таргеты в правильную форму для CrossEntropyLoss
            # CrossEntropyLoss ожидает: input=(N, C), target=(N,) или (N, 1)
            logits = logits.squeeze(-1)     # (batch_size, seq_len-1, vocab_size) → (batch_size*seq_len-1, vocab_size)
            targets = targets.squeeze(0)    # (batch_size, 1) → (batch_size,)
            
            # Вычисляем потери
            loss = criterion(logits, targets)
            
            # Backpropagation
            optimizer.zero_grad()           # Очистка градиентов
            loss.backward()                # Вычисление градиентов
            
            # Обновление весов модели
            optimizer.step()
            
            total_loss += loss.item()
            min_epoch_loss = min(min_epoch_loss, loss.item())
        
        # Среднее значение потерь на эпоху
        avg_loss = total_loss / len(dataloader)
        
        # Вывод результата на текущей эпохе
        print(f"   Эпоха {epoch:2d}/{epochs} | Loss: {avg_loss:.4f} (min: {min_epoch_loss:.4f})")

    print("\n✅ Обучение завершено!")
    
    return model


def main():
    """Основная функция запуска обучения."""
    # Запуск обучения на 15 эпох
    model = train_model(epochs=15, batch_size=32, seq_len=64)

    # Сохранение чекпоинта модели (создаем checkpoint в working directory)
    import os
    
    checkpoint_path = "model_checkpoint.pt"
    torch.save({
        'model_state_dict': model.state_dict(),
        'vocab_size': 256,
        'embed_dim': 64,
        'num_heads': 8,
    }, checkpoint_path)
    
    print(f"\n💾 Чекпоинт сохранен в: {checkpoint_path}")


if __name__ == "__main__":
    main()