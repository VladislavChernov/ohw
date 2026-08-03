import torch
import os

def main():
    """
    Основная функция приложения. Проверяет доступность PyTorch,
    CUDA и GPU для инициации LLM-рабочего процесса.
    """
    print("=============================================")
    print("Старт системы разработки локальной LLM (Bionic Mode)")

    # --- 1. Тест совместимости с CUDA/GPU ---
    if torch.cuda.is_available():
        device = "cuda"
        gpu_count = torch.cuda.device_count()
        print(f"\n✅ Успех! Обнаружен GPU. Доступно устройств: {gpu_count}")
        print(f"🚀 Используется устройство CUDA: {torch.cuda.get_device_name(0)}")
    else:
        device = "cpu"
        print("\n⚠️ Внимание! PyTorch не обнаружил доступный GPU (CUDA).")
        print("   Принудительно переключаемся на использование CPU. Обучение будет медленным.")

    # --- 2. Тест базовой функциональности LLM ---
    try:
        # Проверка, что библиотека transformers может быть инициализирована
        import transformers
        print(f"✅ Библиотека 'transformers' успешно импортирована.")
    except ImportError:
        print("❌ Ошибка: Не удалось импортировать библиотеку 'transformers'.")

    # --- 3. Симуляция запуска LLM ---
    print("\n=============================================")
    if device == "cuda":
        print("✅ ВСЕ КОМПОНЕНТЫ ГОТОВЫ.")
        print("Система готова к загрузке модели и началу обучения LLM на GPU!")
    else:
        print("❌ Предупреждение! Запуск только в режиме CPU. Требуется проверка настройки Docker/NVIDIA.")

if __name__ == "__main__":
    main()