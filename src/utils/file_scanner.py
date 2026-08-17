"""Универсальный сканер файлов в директории. Определяет все пути, которые нужно обработать."""
import os
import glob
from typing import List, Dict

def scan_directory(data_dir: str, supported_extensions: list[str]) -> dict[str, list[str]]:
    """
    Сканирует указанную директорию и группирует найденные пути файлов по расширениям.
    
    Args:
        data_dir (str): Директория для сканирования.
        supported_extensions (list[str]): Список ожидаемых расширений (например, ['.txt', '.pdf']).

    Returns:
        dict[str, list[str]]: Словарь {расширение: [путь1, путь2, ...]}
    """
    file_map: dict[str, list[str]] = {} # Инициализируем пустой словарь для результатов
    
    if not os.path.isdir(data_dir):
        raise FileNotFoundError(f"Директория данных не найдена по пути: {data_dir}")

    print(f"\n🔎 Сканирование директории: {data_dir}")
    
    for ext in supported_extensions:
        # Используем glob для поиска файлов по шаблону
        search_path = os.path.join(data_dir, f"*{ext}")
        file_paths = glob.glob(search_path) # Результат — список полных путей
        file_map[ext] = file_paths

    return file_map