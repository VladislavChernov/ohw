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
