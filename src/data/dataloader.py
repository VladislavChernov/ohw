"""
Модуль данных: Подготовка датасета из Story.txt и DataLoader.

Функциональность обновлена для сканирования ВСЕХ текстовых файлов в заданной директории (DATA_DIR) 
и конкатенации их содержимого для создания единого обучающего корпуса.
"""
import torch
from torch.utils.data import Dataset, DataLoader
from config import SEQ_LEN, BATCH_SIZE, DATA_DIR # Импорт констант

class StoryDataset(Dataset):
    """
    Датасет на основе объединенного текста из всех TXT файлов в DATA_DIR.
    ... (описание остается прежним) ...
    """

    def __init__(self, text: str, tokenizer, seq_len: int):
        """
        Инициализация датасета. Принимает уже объединенный текст.
        """
        self.seq_len = seq_len
        self.text_ids = tokenizer.encode_text(text)  # Полная последовательность токенов
        
        # Создаем пары (input, target)
        self.data = []
        for i in range(len(self.text_ids) - self.seq_len + 1):
            input_seq = torch.tensor(self.text_ids[i:i + self.seq_len - 1], dtype=torch.long)
            target = torch.tensor([self.text_ids[i + self_len-1]], dtype=torch.long)
            self.data.append((input_seq, target))

        print(f"✅ Датасет создан из {len(self.data)} примеров")
        print(f"   Каждая последовательность: input={self.seq_len-1} токенов → target=1 токен")

    def __getitem__(self, idx):
        """Возвращает пару (input_sequence, target_token)."""
        return self.data[idx]

    def __len__(self):
        """Возвращает количество примеров в датасете."""
        return len(self.data)


def get_data_loader(tokenizer, seq_len: int, batch_size: int):
    """
    Функция-фабрика для создания DataLoader из всех файлов в DATA_DIR.
    
    Args:
        tokenizer: Объект токенизатора (он знает, как обрабатывать текст).
        seq_len: Длина последовательности.
        batch_size: Размер батча.
    """
    full_text = ""
    data_files = []

    # 1. Поиск всех TXT файлов в DATA_DIR
    import glob
    search_path = os.path.join(DATA_DIR, "*.txt")
    data_files = glob.glob(search_path)
    
    if not data_files:
        raise FileNotFoundError(f"❌ Критическая ошибка: Не найдено ни одного TXT файла в директории '{DATA_DIR}'. Проверьте путь и файлы.")

    print(f"\n🔎 Найдено {len(data_files)} файлов для чтения...")
    
    # 2. Конкатенация (Объединение) данных
    for file_path in data_files:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                text = f.read()
                full_text += text + "\n" # Добавляем разделитель между файлами
        except Exception as e:
            print(f"⚠️ Предупреждение: Не удалось прочитать файл {file_path}. Ошибка: {e}")

    if not full_text.strip():
        raise ValueError("❌ Критическая ошибка: Все найденные файлы были пустыми.")


    # 3. Токенизация и создание Dataset
    dataset = StoryDataset(full_text, tokenizer, seq_len=seq_len)
    
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
    print("--- ЗАПУСК ТЕСТА DATALOADER ---")
    tokenizer = CharacterTokenizer()
    try:
        dataloader = get_data_loader(tokenizer, seq_len=SEQ_LEN, batch_size=BATCH_SIZE)
        for batch_idx, (inputs, targets) in enumerate(dataloader):
            if batch_idx < 2:  # Выводим только первые два батча для отладки
                print(f"\n📦 Батч {batch_idx}:")
                print(f"   Input shape: {inputs.shape}")    # (batch_size, seq_len-1)
                print(f"   Target shape: {targets.shape}")  # (batch_size, 1)
            break
        print("\n✅ Тест dataloader прошел успешно!")

    except (FileNotFoundError, ValueError) as e:
         print(e)
