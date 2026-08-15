"""Модуль тренера: Содержит цикл обучения и всю логику оптимизации."""

import torch
from torch import nn
import os
from datetime import datetime
from config import (
    TRAINING_EPOCHS, LEARNING_RATE, DATA_DIR, LOG_DIR
)
from src.model import TransformerModel
from src.data import DataLoader


class LLMTrainer:
    """Класс тренера для обучения модели LLM."""

    def __init__(self, model: TransformerModel, data_loader: DataLoader, learning_rate: float = LEARNING_RATE):
        """
        Инициализирует тренера и оптимизатор.

        Аргументы:
            model: Обучаемая модель TransformerModel
            data_loader: DataLoader с датасетом
            learning_rate: Скорость обучения (по умолчанию из config.py)
        """
        print("⚙️ Инициализация Trainer: Настройка Оптимизатора.")
        self.model = model
        # Используем Adam - самый распространенный алгоритм оптимизации
        self.optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
        # CrossEntropyLoss — стандартная функция потерь для задач классификации токенов (LLM).
        self.loss_fn = nn.CrossEntropyLoss()
        self.data_loader = data_loader
        self.epochs = TRAINING_EPOCHS
        self.log_dir = LOG_DIR

    def train_epoch(self):
        """
        Один epoch обучения.

        Возвращает:
            Средняя потеря за epoch (loss)
        """
        self.model.train()  # Включаем режим обучения
        total_loss = 0.0
        num_batches = 0

        for batch_idx, (inputs, targets) in enumerate(self.data_loader):
            inputs, targets = inputs.to(torch.device("cuda" if torch.cuda.is_available() else "cpu")), \
                             targets.to(torch.device("cuda" if torch.cuda.is_available() else "cpu"))

            # Прямой проход
            logits = self.model(inputs)  # (batch_size, seq_len, vocab_size)

            # Подготовка целевых значений для CrossEntropyLoss
            # Берём последний токен из последовательности: (batch_size, seq_len-1) -> (batch_size,)
            targets_flat = targets[:, -inputs.shape[1] - 1:] if inputs.shape[1] > 0 else targets
            targets_flat = targets_flat.view(-1)

            # Вычисляем потерю
            loss = self.loss_fn(logits.view(-1, self.model.vocab_size), targets_flat)

            # Обратный проход
            self.optimizer.zero_grad()
            loss.backward()
            self.optimizer.step()

            total_loss += loss.item()
            num_batches += 1

        avg_loss = total_loss / num_batches
        print(f"   Epoch завершён. Средний loss за epoch: {avg_loss:.4f}")
        return avg_loss

    def train(self):
        """
        Запускает полный цикл обучения.

        Возвращает:
            История потерь по эпохам
        """
        print(f"\n📚 Начало обучения: {self.epochs} epoch")
        losses_history = []

        for epoch in range(1, self.epochs + 1):
            loss = self.train_epoch()
            losses_history.append(loss)
            # Сохраняем прогресс каждую эпоху (опционально)
            if epoch % 1 == 0:
                self.save_model(f"model_epoch_{epoch}.pt")

        print(f"\n✅ Обучение завершено. Средняя потеря: {sum(losses_history)/len(losses_history):.4f}")
        return losses_history

    def save_model(self, filename: str = "model.pt"):
        """
        Сохраняет модель в файл.

        Аргументы:
            filename: Имя файла для сохранения (опционально)
        """
        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir)

        filepath = os.path.join(self.log_dir, filename)
        torch.save({
            'model_state_dict': self.model.state_dict(),
            'optimizer_state_dict': self.optimizer.state_dict(),
            'epoch': self.epochs,
            'loss_fn': self.loss_fn,
        }, filepath)
        print(f"   💾 Модель сохранена в: {filepath}")

    def load_model(self, filepath: str):
        """
        Загружает модель из файла.

        Аргументы:
            filepath: Путь к файлу с сохранённой моделью
        """
        checkpoint = torch.load(filepath)
        self.model.load_state_dict(checkpoint['model_state_dict'])
        self.loss_fn = checkpoint.get('loss_fn', nn.CrossEntropyLoss())
        print(f"   ✅ Модель загружена из: {filepath}")


def train(model: TransformerModel, data_loader: DataLoader):
    """
    Функция-обёртка для запуска обучения.

    Аргументы:
        model: Обучаемая модель
        data_loader: DataLoader с датасетом

    Возвращает:
        История потерь по эпохам
    """
    trainer = LLMTrainer(model=model, data_loader=data_loader)
    return trainer.train()


if __name__ == "__main__":
    # Тестовый запуск обучения (можно запустить отдельно от main_app.py)
    from src.data import DataLoader
    from config import VOCAB_SIZE

    print("🧪 Тест Trainer...")
    model = TransformerModel(vocab_size=VOCAB_SIZE, seq_len=SEQ_LEN)
    data_loader = DataLoader(TRAINING_FILE)  # Из config.py

    losses = train(model, data_loader)
    print("\n✅ Тест Trainer прошёл успешно!")