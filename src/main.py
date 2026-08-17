"""Основная точка входа приложения LLM. Оркестрирует проверку среды, обучение и генерацию текста."""
import torch
import os
import sys
from src.utils import env_check
from config import *
from src.utils.file_scanner import scan_directory
from src.data.dataloader import get_data_loader


def main():
    """
    Главная функция запуска всего рабочего процесса LLM:
    1. Проверка среды (Environment Check)
    2. Обучение модели (Training) - Если указан режим 'train'
    3. Генерация текста (Inference/Generation) - Если указан режим 'generate' с промптом.
    """
    # --- 1. Проверка среды и получение устройства ---
    device, is_ready = env_check.check_environment()

    if not is_ready:
        print("\n?? Невозможно продолжить рабочий процесс из-за ошибок в среде.")
        return

    # --- Обработка аргументов командной строки для выбора режима работы ---
    mode = sys.argv[1].lower()
    
    # Поиск аргумента --prompt
    prompt_arg = None
    if len(sys.argv) > 2:
        for i in range(2, len(sys.argv)):
            if sys.argv[i] == "--prompt" and i + 1 < len(sys.argv):
                prompt_arg = sys.argv[i+1]
                break

    if mode == "train":
        run_training(device)
    elif mode == "generate":
        # Запуск генерации с промптом, если он задан
        if prompt_arg:
            print(f"\n?? Генерация текста по запросу (Промпт): '{prompt_arg}'")
            generate_text_from_prompt(device, prompt_arg)
        else:
            print("\n[Ошибка]: В режиме 'generate' необходимо указать стартовый промптом через аргумент --prompt \"Ваш текст\".")


def run_training(device):
    """Функция, управляющая полным циклом обучения."""
    # 1. Инициализация токенизатора и данных
    print("\n[??]: Начинаем подготовку данных...")

    print("\n[??]: Запуск сканирования и агрегации данных...")

    # 1. Сканируем все поддерживаемые файлы в DATA_DIR для получения путей
    all_files = scan_directory(DATA_DIR, SUPPORTED_EXTENSIONS)

    # 2. Создаем DataLoader, который сам обработает список файлов и создаст датасет
    dataloader = get_data_loader(
        file_paths=all_files, # Передаем путь к файлам вместо текста
        tokenizer=CharacterTokenizer(vocab_size=MAX_VOCAB_SIZE), 
        seq_len=SEQ_LEN,
        batch_size=BATCH_SIZE
    )
        
    # 3. Инициализация модели
    model = TransformerModel(
        vocab_size=tokenizer.get_vocab_size(), 
        embed_dim=EMBEDDING_DIM, 
        num_heads=NUM_HEADS
    ).to(device) # Перемещаем модель на устройство

    # --- 4. Настройка и запуск тренера ---
    trainer = LLMTrainer(model=model, data_loader=dataloader)

    # Вызываем обучение и получаем обученную модель обратно
    trained_model = trainer.train(num_epochs=TRAINING_EPOCHS, log_dir=LOG_DIR)
    
    print("\n? Фаза ОБУЧЕНИЯ завершена. Модель готова к использованию.")
    # Сохранение чекпоинта (ВАЖНО!)
    checkpoint_path = "model_checkpoint.pt"
    torch.save({
        'model_state_dict': trained_model.state_dict(),
        'vocab_size': tokenizer.get_vocab_size(),
        'embed_dim': EMBEDDING_DIM,
        'num_heads': NUM_HEADS,
    }, checkpoint_path)
    print(f"\n?? Чекпоинт модели сохранен в: {checkpoint_path}")


def generate_text_from_prompt(device, prompt_text):
    """Запускает генерацию текста на основе предоставленного пользователем промпта."""
    # Временные переменные для загрузки метаданных из checkpoint.pt
    loaded_config = {'vocab_size': 256, 'embed_dim': EMBEDDING_DIM, 'num_heads': NUM_HEADS}

    try:
        print("\n[??]: Загрузка сохраненной модели для инференса...")
        
        model = TransformerModel(
            vocab_size=loaded_config['vocab_size'], 
            embed_dim=loaded_config['embed_dim'], 
            num_heads=loaded_config['num_heads']
        )

        # Пытаемся загрузить состояние, предполагая, что checkpoint.pt содержит все необходимые метаданные
        model.load_state_dict(torch.load("model_checkpoint.pt", map_location=device))
        model.to(device)

        # Токенизатор должен быть инициализирован с тем же словарем, что и при обучении
        tokenizer = CharacterTokenizer(vocab_size=MAX_VOCAB_SIZE) 

    except Exception as e:
        print(f"\n?? Не удалось загрузить модель или токенизатор. Убедитесь, что вы сначала запустили 'python src/main.py train' и была создана папка training_logs.")
        print(f"Ошибка загрузки: {e}")
        return

    # 3. Подготовка seed из промпта (Промпт -> Идентификаторы)
    seed_ids = tokenizer.encode_text(prompt_text)

    # 4. Вызов генерации
    from src.core import inference as gen_inference
    generated_text = gen_inference.generate_text(model, tokenizer=tokenizer, start_seed=seed_ids, max_length=GENERATION_MAX_TOKENS)
    
    if generated_text:
        print("\n?? Сгенерированный текст (? 40 токенов):")
        print("-" * 50)
        print(generated_text)
        print("-" * 50)


if __name__ == "__main__":
    # Вызываем утилиту проверки среды и затем сам главный процесс
    env_check.check_environment()

    # --- Обработка аргументов ---
    mode = sys.argv[1].lower()
    
    if mode == "train":
        run_training(device)
    elif mode == "generate":
        # Проверка наличия аргумента --prompt
        prompt_arg = None
        for i in range(2, len(sys.argv)):
            if sys.argv[i] == "--prompt" and i + 1 < len(sys.argv):
                prompt_arg = sys.argv[i+1]
                break

        if prompt_arg:
            generate_text_from_prompt(device, prompt_arg)
        else:
            print("\n[Ошибка]: В режиме 'generate' необходимо указать стартовым промптом через аргумент --prompt \"Ваш текст\".")
