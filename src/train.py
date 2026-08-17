"""
Модуль обучения: Альтернативная точка входа для запуска цикла обучения.

Требования к обучению (по книге Рашка "LLMs from Scratch"):
- Модель: простой трансформер на один слой (Embedding + Self-Attention -> Dense)
- Данные: все файлы из DATA_DIR (фабрика обработчиков), по умолчанию data/story.txt
- Функция потерь: CrossEntropyLoss
- Оптимизатор: Adam с learning_rate = 0.001
- Эпох: 15
- На каждой эпохе выводить значение loss

Запуск:
    python src/train.py
"""

import torch
from config import (
    MAX_VOCAB_SIZE, EMBEDDING_DIM, NUM_HEADS,
    SEQ_LEN, BATCH_SIZE, TRAINING_EPOCHS, LOG_DIR,
)
from src.tokenizer import CharacterTokenizer
from src.data.dataloader import get_data_loader
from src.model import TransformerModel
from src.core.trainer import LLMTrainer


def train_model(epochs: int = TRAINING_EPOCHS, batch_size: int = BATCH_SIZE, seq_len: int = SEQ_LEN):
    """
    Обучает модель на корпусе из DATA_DIR.

    Args:
        epochs: Количество эпох обучения (по умолчанию 15)
        batch_size: Размер батча (по умолчанию 32)
        seq_len: Длина последовательности (по умолчанию 64)

    Returns:
        (обученная модель, токенизатор)
    """
    print("=" * 60)
    print("НАЧАЛО ОБУЧЕНИЯ LLM")
    print(f"   Модель: TransformerModel (Embedding + Self-Attention -> Dense)")
    print(f"   Seq len: {seq_len} | Batch size: {batch_size} | Epochs: {epochs}")
    print("=" * 60)

    # 1. Подготовка данных и токенизатора (фабрика обработчиков + построение словаря)
    tokenizer = CharacterTokenizer(max_vocab_size=MAX_VOCAB_SIZE)
    dataloader = get_data_loader(tokenizer, seq_len=seq_len, batch_size=batch_size)

    print(f"\nДатасет готов: {len(dataloader.dataset)} примеров")

    # 2. Инициализация модели и выбор устройства
    model = TransformerModel(
        vocab_size=tokenizer.get_vocab_size(),
        embed_dim=EMBEDDING_DIM,
        num_heads=NUM_HEADS,
    )
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"\nРаботаем на устройстве: {device}")

    # 3. Обучение через LLMTrainer (CrossEntropyLoss + Adam, loss выводится каждую эпоху)
    trainer = LLMTrainer(model=model, data_loader=dataloader, device=device)
    trainer.train(num_epochs=epochs, log_dir=LOG_DIR)

    print("\nОбучение завершено!")
    return model, tokenizer


def main():
    """Основная функция запуска обучения."""
    model, tokenizer = train_model()

    # Сохранение чекпоинта модели (в рабочей директории)
    checkpoint_path = "model_checkpoint.pt"
    torch.save({
        'model_state_dict': model.state_dict(),
        'vocab': tokenizer.vocab,
        'vocab_size': tokenizer.get_vocab_size(),
        'embed_dim': EMBEDDING_DIM,
        'num_heads': NUM_HEADS,
    }, checkpoint_path)

    print(f"\nЧекпоинт сохранен в: {checkpoint_path}")


if __name__ == "__main__":
    main()