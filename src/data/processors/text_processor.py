"""Обработчик текстовых файлов (.txt)."""
import os


def process_single_file(file_path: str) -> tuple[str, str]:
    """
    Читает текст из одного TXT файла.

    Args:
        file_path (str): Полный путь к файлу.

    Returns:
        tuple[str, str]: Содержимое текста и имя файла.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Файл не найден: {file_path}")

    print(f"[Processor] Обработка TXT-файла: {os.path.basename(file_path)}")
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()
        return text, os.path.basename(file_path)
    except Exception as e:
        raise IOError(f"Не удалось прочитать TXT файл {file_path}: {e}")
