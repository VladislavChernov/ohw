"""Утилиты для проверки и подготовки среды LLM."""
import torch
from typing import Tuple


def check_environment() -> Tuple[str, bool]:
    """
    Проверяет доступность GPU (CUDA) и основных зависимостей.

    Returns:
        (device, is_ready): устройство ('cuda'/'cpu') и статус готовности.
    """
    print("=============================================")
    print("Проверка среды разработки LLM")

    # 1. Тест совместимости с CUDA/GPU
    if torch.cuda.is_available():
        device = "cuda"
        gpu_count = torch.cuda.device_count()
        print(f"OK! Обнаружен GPU. Доступно устройств: {gpu_count}")
        print(f"Используется CUDA: {torch.cuda.get_device_name(0)}")
    else:
        device = "cpu"
        print("Внимание! PyTorch не обнаружил доступный GPU (CUDA).")
        print("Используется CPU. Обучение будет медленным, но проект работоспособен.")

    # 2. Проверка базовых зависимостей (необязательные библиотеки - не блокируют запуск)
    try:
        import transformers
        print(f"OK! Библиотека 'transformers' доступна.")
    except ImportError:
        print("Предупреждение: библиотека 'transformers' не установлена (не обязательна для работы).")

    # 3. Определение готовности: проект работает и на GPU, и на CPU
    is_ready = True
    print("=============================================")
    print(f"СРЕДА ГОТОВА. Устройство: {device}")

    return device, is_ready