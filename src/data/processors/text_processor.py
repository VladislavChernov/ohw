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


def process_all_files(data_dir: str, file_paths: list[str], ext: str):
    """Сканирует список путей и возвращает список (текст, имя файла)."""
    processed_data = []
    print(f"\n🔎 Начинается сканирование TXT файлов...")

    for file_path in file_paths:
        try:
            # Теперь вызываем обработчик для каждого пути индивидуально
            text, filename = process_single_file(file_path) 
            processed_data.append((text, filename))
        except Exception as e:
            print(f"⚠️ Пропуск файла {file_path} из-за ошибки при обработке: {e}")

    return processed_data