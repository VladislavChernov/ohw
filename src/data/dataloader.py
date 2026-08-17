"""Модуль данных: Подготовка датасета из всех текстовых файлов и DataLoader.

Функциональность построена на паттерне Фабрики обработчиков (Dispatcher):
сканируются ВСЕ файлы в заданной директории (DATA_DIR) по расширениям
и их содержимое конкатенируется в единый корпус текста.

Замечание: обработчики .pdf и .docx являются заглушками (примерами того,
как может быть расширен проект) и возвращают placeholder-текст.
"""
import os

import torch
from torch.utils.data import Dataset, DataLoader
from config import SEQ_LEN, BATCH_SIZE, DATA_DIR, SUPPORTED_EXTENSIONS

# Импорт всех необходимых модулей и сканера:
from src.utils.file_scanner import scan_directory  # <-- Главный импорт сканирования
from src.data.processors.text_processor import process_all_files as _process_txt
from src.data.processors.pdf_processor import process_all_files as _process_pdf
from src.data.processors.docx_processor import process_all_files as _process_docx


class StoryDataset(Dataset):
    """
    Датасет на основе объединенного текста из всех TXT, PDF и DOCX файлов в DATA_DIR.

    Каждый пример: (input_seq, target_seq), где target_seq - это input_seq,
    сдвинутый на один токен вперед (teacher-forcing для предсказания следующего токена).
    """

    def __init__(self, text: str, tokenizer, seq_len: int):
        """
        Инициализация датасета. Принимает уже объединенный текст.

        Args:
            text: Полный текст корпуса.
            tokenizer: Токенизатор с построенным словарем.
            seq_len: Длина последовательности (вход и target одинаковой длины).
        """
        self.seq_len = seq_len
        self.text_ids = tokenizer.encode_text(text)  # Полная последовательность токенов

        # Создаем пары (input_seq, target_seq): target сдвинут на 1 токен
        self.data = []
        for i in range(len(self.text_ids) - self.seq_len):
            input_seq = torch.tensor(self.text_ids[i:i + self.seq_len], dtype=torch.long)
            target_seq = torch.tensor(self.text_ids[i + 1:i + self.seq_len + 1], dtype=torch.long)
            self.data.append((input_seq, target_seq))

        if not self.data:
            raise ValueError("Текст слишком короткий для создания примеров с заданным seq_len.")

        print(f"Датасет создан из {len(self.data)} примеров")
        print(f"   Каждая пара: input={self.seq_len} токенов -> target={self.seq_len} токенов (со сдвигом на 1)")

    def __len__(self) -> int:
        return len(self.data)

    def __getitem__(self, idx: int):
        return self.data[idx]


def load_corpus_text() -> str:
    """
    Сканирует DATA_DIR и собирает текст из всех поддерживаемых файлов
    с помощью Фабрики обработчиков (Dispatcher).

    Returns:
        Строка - единый корпус текста.

    Raises:
        FileNotFoundError: если не удалось загрузить текст ни из одного источника.
    """
    all_processed_data = []

    print("--- Начинается фаза сканирования и парсинга источников данных ---")

    # 1. СКАНИРОВАНИЕ: Вызываем центральный сканер для получения всех путей.
    file_map = scan_directory(DATA_DIR, SUPPORTED_EXTENSIONS)

    # 2. ДИСПЕТЧЕРИЗАЦИЯ: Используем маппинг для вызова нужного обработчика.
    processor_map = {
        ".txt": _process_txt,
        ".pdf": _process_pdf,
        ".docx": _process_docx,
    }

    for ext in SUPPORTED_EXTENSIONS:
        # Проверяем, существует ли в нашей map обработчик для данного расширения.
        if ext in processor_map:
            try:
                # Вызываем конкретный обработчик, передавая ему все найденные пути
                file_results = processor_map[ext](DATA_DIR, file_map[ext], ext)
                all_processed_data.extend(file_results)
            except Exception as e:
                print(f"Критическая ошибка при вызове обработчика {ext}: {e}")

    if not all_processed_data:
        raise FileNotFoundError(
            "Не удалось загрузить текст ни из одного поддерживаемого источника."
        )

    # 3. КОНКАТЕНАЦИЯ: Объединение всего текста в единый корпус.
    print("\n[INFO]: Обнаружено источников: " + "; ".join(name for _, name in all_processed_data))

    full_text = ""
    for text, name in all_processed_data:
        full_text += text + "\n\n--- END OF SOURCE FILE: " + name + " ---\n\n"

    return full_text


def get_data_loader(tokenizer, seq_len: int, batch_size: int, text: str | None = None):
    """
    Фабричный метод - загрузчик данных.

    Если text не передан, сканирует всю директорию DATA_DIR и собирает корпус
    через Фабрику обработчиков. Затем строит словарь токенизатора на корпусе,
    создает датасет и DataLoader.

    Args:
        tokenizer: Токенизатор (CharacterTokenizer). Словарь будет построен на корпусе.
        seq_len: Длина последовательности.
        batch_size: Размер батча.
        text: Необязательный готовый корпус текста (если передан, сканирование пропускается).

    Returns:
        torch.utils.data.DataLoader
    """
    if text is None:
        text = load_corpus_text()

    # Строим словарь токенизатора на корпусе (индекс 0 - <unk>)
    tokenizer.build_vocab(text)

    dataset = StoryDataset(text, tokenizer, seq_len=seq_len)

    dataloader = DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=False,
        # Отбрасываем неполный последний батч, только если примеров больше одного батча
        drop_last=len(dataset) > batch_size,
    )

    print(f"DataLoader создан: {len(dataset)} примеров / {batch_size} на батч")
    return dataloader


if __name__ == "__main__":
    from src.tokenizer import CharacterTokenizer

    # Тестирование датасета
    print("--- ЗАПУСК ТЕСТА DATALOADER ---")
    tokenizer = CharacterTokenizer()
    try:
        dataloader = get_data_loader(tokenizer, seq_len=SEQ_LEN, batch_size=BATCH_SIZE)
        for batch_idx, (inputs, targets) in enumerate(dataloader):
            if batch_idx < 2:  # Выводим только первые два батча для отладки
                print(f"\nБатч {batch_idx}:")
                print(f"   Input shape: {inputs.shape}")    # (batch_size, seq_len)
                print(f"   Target shape: {targets.shape}")  # (batch_size, seq_len)
            break
        print("\nТест dataloader прошел успешно!")
    except (FileNotFoundError, ValueError) as e:
        print(e)