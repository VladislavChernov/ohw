"""
Модуль данных: Подготовка датасета из story.txt и DataLoader.

Требования к подготовке данных (по книге Рашка "LLMs from Scratch"):
1. Создать словарь токенов из story.txt (character-level)
2. Преобразовать текст в последовательность индексов токенов
3. Разделить на пары (input: seq_len-1 токенов, target: next_token)

Параметры:
- seq_len = 64 (последовательность из 63 токенов + 1 целевой токен)
- batch_size = 32
- shuffle = False для воспроизводимости обучения
"""
import torch
from torch.utils.data import Dataset, DataLoader
from config import SEQ_LEN, BATCH_SIZE # Импорт констант

class StoryDataset(Dataset):
    """
    Датасет на основе текста из story.txt.
    
    Создает пары (input_sequence, target_token), где:
    - input_sequence: последовательность из seq_len-1 токенов
    - target_token: следующий токен в последовательности
    
    Пример для seq_len=64:
        input  : [t0, t1, ..., t62]  (63 токена)
        target : [t63]              (1 токен — следующий)
    """

    def __init__(self, text: str, tokenizer, seq_len: int):
        """
        Инициализация датасета.
        
        Args:
            text: Полный текст из story.txt
            tokenizer: Объект токенизатора с методами encode_text и get_vocab_size
            seq_len: Длина последовательности (включая целевой токен)
        """
        self.seq_len = seq_len
        self.text_ids = tokenizer.encode_text(text)  # Полная последовательность токенов
        
        # Создаем пары (input, target)
        self.data = []
        for i in range(len(self.text_ids) - self.seq_len + 1):
            input_seq = torch.tensor(self.text_ids[i:i + self.seq_len - 1], dtype=torch.long)
            target = torch.tensor([self.text_ids[i + self.seq_len - 1]], dtype=torch.long)
            self.data.append((input_seq, target))

        print(f"✅ Датасет создан из {len(self.data)} примеров")
        print(f"   Каждая последовательность: input={self.seq_len-1} токенов → target=1 токен")

    def __getitem__(self, idx):
        """Возвращает пару (input_sequence, target_token)."""
        return self.data[idx]

    def __len__(self):
        """Возвращает количество примеров в датасете."""
        return len(self.data)


def get_data_loader(text: str, tokenizer, seq_len: int, batch_size: int):
    """
    Создает DataLoader из текста и токенизатора.
    
    Args:
        text: Полный текст для токенизации
        tokenizer: Объект токенизатора
        seq_len: Длина последовательности (включая целевой токен)
        batch_size: Размер батча
        
    Returns:
        DataLoader, готовый к использованию в цикле обучения
    """
    dataset = StoryDataset(text, tokenizer, seq_len=seq_len)
    
    # shuffle=False для воспроизводимости результатов обучения
    dataloader = DataLoader(
        dataset, 
        batch_size=batch_size, 
        shuffle=False, 
        drop_last=True  # Отбрасываем неполный последний батч
    )
    
    print(f"✅ DataLoader создан: {len(dataset)} примеров / {batch_size} на батч")
    return dataloader


if __name__ == "__main__":
    from src.tokenizer import CharacterTokenizer
    import os

    # Тестирование датасета
    tokenizer = CharacterTokenizer()
    
    try:
        with open("data/story.txt", "r", encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
         print("❌ Ошибка: Файл data/story.txt не найден. Невозможно протестировать dataloader.")
         exit(1)

    print(f"📄 Читаю story.txt...")
    dataset = StoryDataset(text, tokenizer, seq_len=SEQ_LEN)
    
    # Вывод примера батча
    dataloader = get_data_loader(text, tokenizer, seq_len=SEQ_LEN, batch_size=BATCH_SIZE)
    for batch_idx, (inputs, targets) in enumerate(dataloader):
        if batch_idx < 2:  # Выводим только первые два батча для отладки
            print(f"\n📦 Батч {batch_idx}:")
            print(f"   Input shape: {inputs.shape}")    # (batch_size, seq_len-1)
            print(f"   Target shape: {targets.shape}")  # (batch_size, 1)
        break
    
    print("\n✅ Тест dataloader прошел успешно!")