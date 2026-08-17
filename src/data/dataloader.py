"""Модуль данных: Подготовка датасета из Story.txt и DataLoader.

Функциональность обновлена для сканирования ВСЕХ текстовых файлов в заданной директории (DATA_DIR) 
и конкатенации их содержимого, используя паттерн Фабрики обработчиков (Dispatcher).
"""
import torch
from torch.utils.data import Dataset, DataLoader
import os
from config import SEQ_LEN, BATCH_SIZE, DATA_DIR, SUPPORTED_EXTENSIONS

# Импорт всех необходимых модулей и сканера:
from src.utils.file_scanner import scan_directory # <-- Главный импорт сканирования
from src.data.processors.text_processor import process_all_files as _process_txt 
from src.data.processors.pdf_processor import process_all_files as _process_pdf
from src.data.processors.docx_processor import process_all_files as _process_docx

class StoryDataset(Dataset):
    """
    Датасет на основе объединенного текста из всех TXT, PDF и DOCX файлов в DATA_DIR.
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


def get_data_loader(tokenizer, seq_len: int, batch_size: int):
    """
    Фабричный метод загрузчик данных. Сканирует всю директорию DATA_DIR 
    и собирает данные из всех поддерживаемых файлов, используя Фабрику обработчиков.
    """
    all_processed_data = []

    print("--- Начинается фаза сканирования и парсинга источников данных ---")

    # 1. СКАНИРОВАНИЕ: Вызываем центральный сканер для получения всех путей.
    try:
        file_map = scan_directory(DATA_DIR, SUPPORTED_EXTENSIONS)
    except FileNotFoundError as e:
        raise e # Перебрасываем ошибку файловой системы

    # 2. Диспетчеризация: Используем маппинг для вызова нужного обработчика.
    processor_map = {
        ".txt": _process_txt,
        ".pdf": _process_pdf,
        ".docx": _process_docx
    }

    for ext in SUPPORTED_EXTENSIONS:
        # Проверяем, существует ли в нашей map обработчик для данного расширения.
        if ext in processor_map:
            processor_func = processor_map[ext]
            try:
                # Вызываем конкретный обработчик, передавая ему все найденные пути
                file_results = processor_func(DATA_DIR, file_map[ext], ext) 
                all_processed_data.extend(file_results)
            except Exception as e:
                 print(f"⚠️ Критическая ошибка при вызове обработчика {ext}: {e}")


    if not all_processed_data:
        raise FileNotFoundError("❌ Критическая ошибка: Не удалось загрузить текст из ни одного поддерживаемого источника.")

    # 3. Конкатенация данных (Объединение всего текста)
    full_text = ""
    print("\n[💡]: Обнаружено источников: " + "; ".join([os.path.basename(d) for _, d in all_processed_data]))

    for text, name in all_processed_data:
        # Добавляем разделитель и конкатенируем текст из всех файлов
        full_text += text + "\n\n--- END OF SOURCE FILE: " + name + " ---\n\n"

    # 4. Токенизация и создание Dataset
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