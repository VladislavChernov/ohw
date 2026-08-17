"""Модуль тренера: Содержит цикл обучения и всю логику оптимизации."""

import os

import torch
from torch import nn
from torch.utils.data import DataLoader
from config import TRAINING_EPOCHS, LEARNING_RATE, LOG_DIR
from src.model import TransformerModel


class LLMTrainer:
    """Класс тренера для обучения модели LLM."""

    def __init__(self, model: TransformerModel, data_loader: DataLoader,
                 learning_rate: float = LEARNING_RATE, device: str | None = None):
        """
        Инициализирует тренера и оптимизатор.

        Аргументы:
            model: Обучаемая модель TransformerModel
            data_loader: DataLoader с датасетом
            learning_rate: Скорость обучения (по умолчанию из config.py)
            device: Устройство ('cuda'/'cpu'). Если None - определяется автоматически.
        """
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model = model.to(self.device)
        # Используем Adam - самый распространенный алгоритм оптимизации
        self.optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
        # CrossEntropyLoss - стандартная функция потерь для задач классификации токенов (LLM).
        self.loss_fn = nn.CrossEntropyLoss()
        self.data_loader = data_loader
        self.epochs = TRAINING_EPOCHS
        self.log_dir = LOG_DIR

    def train_epoch(self):
        """
        Один epoch обучения.

        Returns:
            Средняя потеря (loss) за epoch.
        """
        self.model.train()  # Включаем режим обучения
        total_loss = 0.0
        num_batches = 0

        for batch_idx, (inputs, targets) in enumerate(self.data_loader):
            inputs = inputs.to(self.device)
            targets = targets.to(self.device)

            # Прямой проход: (batch_size, seq_len, vocab_size)
            logits = self.model(inputs)

            # CrossEntropyLoss ожидает (N, C) логиты и (N) таргеты.
            # target_seq - это input_seq, сдвинутый на 1 токен (teacher-forcing).
            loss = self.loss_fn(logits.view(-1, self.model.vocab_size), targets.view(-1))

            # Обратный проход
            self.optimizer.zero_grad()
            loss.backward()
            self.optimizer.step()

            total_loss += loss.item()
            num_batches += 1

        avg_loss = total_loss / max(num_batches, 1)
        return avg_loss

    def train(self, num_epochs: int | None = None, log_dir: str | None = None):
        """
        Запускает полный цикл обучения.

        Аргументы:
            num_epochs: Количество эпох (по умолчанию из config.py).
            log_dir: Директория для сохранения чекпоинтов (по умолчанию из config.py).

        Returns:
            История потерь по эпохам.
        """
        num_epochs = num_epochs or self.epochs
        log_dir = log_dir or self.log_dir

        print(f"\nНачало обучения: {num_epochs} epochs (device: {self.device})")
        losses_history = []

        for epoch in range(1, num_epochs + 1):
            loss = self.train_epoch()
            losses_history.append(loss)
            print(f"   Эпоха {epoch:2d}/{num_epochs} | Loss: {loss:.4f}")

            # Сохраняем прогресс каждую эпоху
            self.save_model(f"model_epoch_{epoch}.pt", log_dir=log_dir)

        print(f"\nОбучение завершено. Средняя потеря: {sum(losses_history) / len(losses_history):.4f}")
        return losses_history

    def save_model(self, filename: str = "model.pt", log_dir: str | None = None):
        """
        Сохраняет модель в файл.

        Аргументы:
            filename: Имя файла для сохранения (опционально)
            log_dir: Директория сохранения (по умолчанию из config.py)
        """
        log_dir = log_dir or self.log_dir
        os.makedirs(log_dir, exist_ok=True)

        filepath = os.path.join(log_dir, filename)
        torch.save({
            'model_state_dict': self.model.state_dict(),
            'optimizer_state_dict': self.optimizer.state_dict(),
            'vocab_size': self.model.vocab_size,
            'embed_dim': self.model.embed_dim,
            'num_heads': self.model.num_heads,
        }, filepath)
        print(f"   Модель сохранена в: {filepath}")

    def load_model(self, filepath: str):
        """
        Загружает модель из файла.

        Аргументы:
            filepath: Путь к файлу с сохранённой моделью
        """
        checkpoint = torch.load(filepath, map_location=self.device)
        self.model.load_state_dict(checkpoint['model_state_dict'])
        print(f"   Модель загружена из: {filepath}")


def train(model: TransformerModel, data_loader: DataLoader, num_epochs: int = TRAINING_EPOCHS):
    """
    Функция-обёртка для запуска обучения.

    Аргументы:
        model: Обучаемая модель
        data_loader: DataLoader с датасетом
        num_epochs: Количество эпох

    Returns:
        История потерь по эпохам.
    """
    trainer = LLMTrainer(model=model, data_loader=data_loader)
    return trainer.train(num_epochs=num_epochs)