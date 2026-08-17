"""Основная точка входа приложения LLM. Оркестрирует проверку среды, обучение и генерацию текста."""
import sys

import torch
from config import (
    LOG_DIR,
    TRAINING_EPOCHS, BATCH_SIZE, SEQ_LEN,
    MAX_VOCAB_SIZE, EMBEDDING_DIM, NUM_HEADS,
    GENERATION_MAX_TOKENS, SEED_LENGTH,
)
from src.utils import env_check
from src.tokenizer import CharacterTokenizer
from src.data.dataloader import get_data_loader
from src.model import TransformerModel
from src.core.trainer import LLMTrainer
from src.core.inference import generate_text


def parse_prompt_arg(argv: list[str]) -> str | None:
    """Ищет аргумент --prompt "<текст>" в списке аргументов командной строки."""
    if "--prompt" in argv:
        idx = argv.index("--prompt")
        if idx + 1 < len(argv):
            return argv[idx + 1]
    return None


def main():
    """
    Главная функция запуска всего рабочего процесса LLM:
    1. Проверка среды (Environment Check)
    2. Обучение модели (Training) - если указан режим 'train'
    3. Генерация текста (Inference/Generation) - если указан режим 'generate' с промптом.
    """
    # --- 1. Проверка среды и получение устройства ---
    device, is_ready = env_check.check_environment()

    if not is_ready:
        print("\nНевозможно продолжить рабочий процесс из-за ошибок в среде.")
        return

    # --- 2. Обработка аргументов командной строки для выбора режима работы ---
    if len(sys.argv) < 2:
        print('Использование: python src/main.py <train|generate> [--prompt "Ваш текст"]')
        return

    mode = sys.argv[1].lower()
    prompt_arg = parse_prompt_arg(sys.argv)

    if mode == "train":
        run_training(device)
    elif mode == "generate":
        if prompt_arg:
            generate_text_from_prompt(device, prompt_arg)
        else:
            print(
                '\n[Ошибка]: В режиме "generate" необходимо указать промпт '
                'через аргумент --prompt "Ваш текст".'
            )
    else:
        print(f"\n[Ошибка]: Неизвестный режим '{mode}'. Доступно: train, generate.")


def run_training(device):
    """Функция, управляющая полным циклом обучения."""
    # 1. Инициализация токенизатора и данных
    print("\n[INFO]: Начинаем подготовку данных...")
    tokenizer = CharacterTokenizer(max_vocab_size=MAX_VOCAB_SIZE)

    print("\n[INFO]: Запуск сканирования и агрегации данных...")
    # DataLoader сам сканирует DATA_DIR, строит словарь и создает датасет
    dataloader = get_data_loader(
        tokenizer=tokenizer,
        seq_len=SEQ_LEN,
        batch_size=BATCH_SIZE,
    )

    # 2. Инициализация модели
    model = TransformerModel(
        vocab_size=tokenizer.get_vocab_size(),
        embed_dim=EMBEDDING_DIM,
        num_heads=NUM_HEADS,
    ).to(device)

    # 3. Настройка и запуск тренера
    trainer = LLMTrainer(model=model, data_loader=dataloader, device=device)

    # Вызываем обучение
    trainer.train(num_epochs=TRAINING_EPOCHS, log_dir=LOG_DIR)

    print("\nФаза ОБУЧЕНИЯ завершена. Модель готова к использованию.")

    # 4. Сохранение чекпоинта (ВАЖНО!)
    checkpoint_path = "model_checkpoint.pt"
    torch.save({
        'model_state_dict': model.state_dict(),
        'vocab': tokenizer.vocab,
        'vocab_size': tokenizer.get_vocab_size(),
        'embed_dim': EMBEDDING_DIM,
        'num_heads': NUM_HEADS,
    }, checkpoint_path)
    print(f"\nЧекпоинт модели сохранен в: {checkpoint_path}")


def generate_text_from_prompt(device, prompt_text):
    """Запускает генерацию текста на основе предоставленного пользователем промпта."""
    try:
        print("\n[INFO]: Загрузка сохраненной модели для инференса...")

        # Загружаем метаданные и веса из чекпоинта
        checkpoint = torch.load("model_checkpoint.pt", map_location=device)

        model = TransformerModel(
            vocab_size=checkpoint.get("vocab_size", MAX_VOCAB_SIZE),
            embed_dim=checkpoint.get("embed_dim", EMBEDDING_DIM),
            num_heads=checkpoint.get("num_heads", NUM_HEADS),
        )
        model.load_state_dict(checkpoint["model_state_dict"])
        model.to(device)

        # Восстанавливаем токенизатор с тем же словарем, что и при обучении
        tokenizer = CharacterTokenizer(max_vocab_size=MAX_VOCAB_SIZE)
        tokenizer.vocab = checkpoint.get("vocab", tokenizer.vocab)
        tokenizer.inv_vocab = {idx: sym for sym, idx in tokenizer.vocab.items()}

    except Exception as e:
        print('\n[Ошибка]: Не удалось загрузить модель или токенизатор. '
              'Убедитесь, что вы сначала запустили "python src/main.py train".')
        print(f"Ошибка загрузки: {e}")
        return

    # 1. Подготовка seed из промпта (Промпт -> Идентификаторы), ограничиваем длину SEED_LENGTH
    seed_ids = tokenizer.encode_text(prompt_text)[:SEED_LENGTH]
    print(f"\n[INFO]: Сид ({len(seed_ids)} токенов): {tokenizer.decode_ids(seed_ids)!r}")

    # 2. Вызов генерации
    generated_text = generate_text(
        model, tokenizer=tokenizer, start_seed=seed_ids, max_length=GENERATION_MAX_TOKENS
    )

    if generated_text:
        print(f"\n[РЕЗУЛЬТАТ] Сгенерированный текст (+{GENERATION_MAX_TOKENS} новых токенов):")
        print("-" * 50)
        print(generated_text)
        print("-" * 50)


if __name__ == "__main__":
    main()
