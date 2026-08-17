"""Обработчик Word-документов (.docx)."""
import os


def process_single_file(file_path: str) -> tuple[str, str]:
    """
    Извлекает текст из DOCX файла.

    Args:
        file_path (str): Полный путь к файлу.

    Returns:
        tuple[str, str]: Содержимое текста и имя файла.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Файл не найден: {file_path}")

    print(f"[Processor] Обработка DOCX-файла: {os.path.basename(file_path)}")
    # *** TODO: Реализовать парсинг DOCX и вернуть извлеченный текст ***
    return f"DOCX Placeholder Content from {os.path.basename(file_path)}", os.path.basename(file_path)


def process_all_files(data_dir: str, file_paths: list[str], ext: str):
    """Сканирует список путей и возвращает список (текст, имя файла)."""
    processed_data = []
    print("\nНачинается сканирование DOCX файлов...")

    for file_path in file_paths:
        try:
            # Вызываем конкретный обработчик для каждого пути
            text, filename = process_single_file(file_path)
            processed_data.append((text, filename))
        except Exception as e:
            print(f"⚠️ Пропуск файла {file_path} из-за ошибки при обработке: {e}")

    return processed_data
