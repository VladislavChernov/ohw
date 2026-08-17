"""Утилиты для проверки и подготовки среды LLM."""
import torch
from typing import Tuple

def check_environment() -> Tuple[str, bool]:
    """
    Проверяет доступность GPU (CUDA) и других системных требований.
    Возвращает устройство (device) и статус готовности (is_ready).
    """
    print("=============================================")
    print("🔍 Проверка среды разработки LLM")

    # 1. Тест совместимости с CUDA/GPU
    if torch.cuda.is_available():
        device = "cuda"
        gpu_count = torch.cuda.device_count()
        print(f"\n✅ Успех! Обнаружен GPU. Доступно устройств: {gpu_count}")
        print(f"🚀 Используется устройство CUDA: {torch.cuda.get_device_name(0)}")
    else:
        device = "cpu"
        print("\n⚠️ Внимание! PyTorch не обнаружил доступный GPU (CUDA).")
        print("   Принудительно переключаемся на использование CPU. Обучение будет медленным.")

    # 2. Тест базовой функциональности LLM
    try:
        import transformers
        print(f"✅ Библиотека 'transformers' успешно импортирована.")
    except ImportError:
        print("❌ Критическая ошибка: Не удалось импортировать библиотеку 'transformers'.")

    # 3. Определение готовности
    is_ready = True
    if device == "cuda":
        print("\n=============================================")
        print("✅ ВСЕ КОМПОНЕНТЫ ГОТОВЫ.")
        print("Система готова к загрузке модели и началу обучения LLM на GPU!")
    else:
        is_ready = False
        print("\n❌ Предупреждение! Запуск только в режиме CPU. Требуется проверка настройки Docker/NVIDIA.")

    return device, is_ready