"""Универсальный сканер файлов в директории.

Определяет все пути, которые нужно обработать.
"""
import glob
import os


def scan_directory(data_dir: str, supported_extensions: list[str]) -> dict[str, list[str]]:
    """
    Рекурсивно сканирует указанную директорию и группирует найденные пути по расширениям.

    Args:
        data_dir (str): Директория для сканирования.
        supported_extensions (list[str]): Список ожидаемых расширений (например, ['.txt', '.pdf']).

    Returns:
        dict[str, list[str]]: Словарь {расширение: [путь1, путь2, ...]}
    """
    file_map: dict[str, list[str]] = {}  # Инициализируем пустой словарь для результатов

    if not os.path.isdir(data_dir):
        raise FileNotFoundError(f"Директория данных не найдена по пути: {data_dir}")

    print(f"\nСканирование директории: {data_dir}")

    for ext in supported_extensions:
        # Используем glob для поиска файлов по шаблону (рекурсивно по всем поддиректориям)
        search_path = os.path.join(data_dir, "**", f"*{ext}")
        file_paths = sorted(glob.glob(search_path, recursive=True))
        file_map[ext] = file_paths

    return file_map
