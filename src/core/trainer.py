"""Модуль тренера: Содержит цикл обучения и всю логику оптимизации."""
import torch
from torch import nn
import os
from datetime import datetime
from config import TRAINING_EPOCHS, LEARNING_RATE 

# Импорт констант из config.py
from src.model import TransformerModel 
from src.data import DataLoader # Импортируем наш класс для работы с данными


class LLMTrainer:
    def __init__(self, model: TransformerModel, data_loader: DataLoader, learning_rate: float = LEARNING_RATE):
        """Инициализирует тренера и оптимизатор."""
        print("⚙️ Инициализация Trainer: Настройка Оптимизатора.")
        self.model = model
        # Используем Adam - самый распространенный алгоритм оптимизации
        self.optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
        # CrossEntropyLoss — стандартная функция потерь для задач классификации токенов (LLM).
        self.loss_fn = nn.CrossEntropyLoss() 

    def train(self, num_epochs: int, log_dir: str):
        """Основной цикл обучения с логированием результатов. Возвращает обученную модель."""
        print("\n" + "="*60)
        print("🚀 СТАРТ ЦИКЛА ОБУЧЕНИЯ LLM")
        print("="*60)
        
        # Установка модели в режим обучения (важно для Dropout/BatchNorm)
        self.model.train() 

        # Создание директории логов, если она не существует
        os.makedirs(log_dir, exist_ok=True)
        log_filepath = os.path.join(log_dir, "training_loss.csv")

        for epoch in range(1, num_epochs + 1):
            total_loss = 0
            batch_count = 0
            min_epoch_loss = float('inf')
            
            print(f"\n--- Эпоха {epoch}/{num_epochs} ---")

            # Итерация по батчам данных из DataLoader (вместо прямого обращения)
            for i, (inputs, targets) in enumerate(self.data_loader):
                # 1. Обнуление градиентов
                self.optimizer.zero_grad() 
                
                # 2. Forward Pass (Прямой проход)
                logits = self.model(inputs) # (batch_size, seq_len-1, vocab_size)
                
                # 3. Расчет потерь (Loss Calculation)
                predicted_logits = logits[:, -1:, :] 
                loss = self.loss_fn(predicted_logits, targets) 

                total_loss += loss.item()
                batch_count += 1

            avg_loss = total_loss / batch_count if batch_count > 0 else 0
            print(f"Loss on this epoch: {avg_loss:.4f}")
            
            # 4. Backward Pass и Обновление весов (Оптимизация)
            self.optimizer.step() 
            torch.cuda.empty_cache() # Очистка памяти GPU

            # Логирование результата эпохи
            with open(log_filepath, "a") as f:
                f.write(f"{epoch},{avg_loss:.6f}\n")
        
        print("\n=============================================")
        print(f"✅ ОБУЧЕНИЕ ЗАВЕРШЕНО.")
        print(f"Лог-файлы сохранены в папку: {log_dir}")
        return self.model # ВОЗВРАЩАЕМ обученную модель!