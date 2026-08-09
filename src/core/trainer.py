"""Модуль тренера: Содержит цикл обучения и всю логику оптимизации."""
import torch
from torch import nn
# Убедитесь, что импорты корректны, так как это основа всей ML-логики.
from src.model import TransformerModel 
from src.data import DataLoader # Импортируем наш класс для работы с данными

class LLMTrainer:
    def __init__(self, model: TransformerModel, data_loader: DataLoader, learning_rate: float = 1e-4):
        """Инициализирует тренера и оптимизатор."""
        print("⚙️ Инициализация Trainer: Настройка Оптимизатора.")
        self.model = model
        # Используем Adam - самый распространенный алгоритм оптимизации
        self.optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
        # CrossEntropyLoss — стандартная функция потерь для задач классификации токенов (LLM).
        self.loss_fn = nn.CrossEntropyLoss() 

    def train(self, num_epochs: int):
        """Основной цикл обучения."""
        print("\n" + "="*60)
        print("🚀 СТАРТ ЦИКЛА ОБУЧЕНИЯ LLM")
        print("="*60)
        
        for epoch in range(num_epochs):
            total_loss = 0
            batch_count = 0
            
            # --- Итерация по данным (Главный цикл обучения) ---
            # В реальном коде мы бы использовали DataLoader с worker'ами.
            for batch_index in range(1, 11): # Итерируем 10 раз, как задано в __len__
                self.model.train() # Переводим модель в режим обучения (важно для Dropout/BatchNorm)

                # 1. Получение батча данных
                batch = self.data_loader[batch_index - 1]
                
                # 2. Обнуление градиентов
                self.optimizer.zero_grad() 
                
                # 3. Forward Pass (Прямой проход)
                output = self.model(batch) # Вызываем модель с данными
                
                # 4. Расчет потерь (Loss Calculation)
                # TODO: Здесь нужна логика расчета потери на основе сравнения output и истинных меток (target).
                loss = torch.rand(1) * 0.5 
                total_loss += loss
                batch_count += 1

            avg_loss = total_loss / batch_count if batch_count > 0 else 0
            print(f"\n[ЭПОХА {epoch+1}/{num_epochs}]...")
            
            # 5. Backward Pass и Обновление весов
            print(f"Loss calculated: {avg_loss:.4f}. Начинается оптимизация...")
            torch.cuda.empty_cache() # Очистка памяти GPU перед шагом оптимизатора
            self.optimizer.step() 

        print("\n=============================================")
        print(f"✅ ОБУЧЕНИЕ ЗАВЕРШЕНО. Средний Loss: {avg_loss:.4f}")

