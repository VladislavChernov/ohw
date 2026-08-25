"""Модуль для генерации текста (Инференс)."""
import torch
from src.tokenizer import CharacterTokenizer


def generate_text(model, tokenizer: CharacterTokenizer, start_seed: list[int], max_length: int = 40) -> str:
    """
    Генерирует текст авторегрессионным методом на основе заданной начальной последовательности.

    Args:
        model: Обученная модель TransformerModel (в режиме evaluation).
        tokenizer: Инициализированный токенизатор.
        start_seed: Начальная последовательность индексов токенов.
        max_length: Сколько новых токенов сгенерировать (по умолчанию 40).

    Returns:
        Сгенерированная строка текста (сид + max_length новых токенов).
    """
    print("\nНачинаем авторегрессионную генерацию...")
    model.eval()  # Переводим модель в режим оценки (отключает Dropout)
    device = next(model.parameters()).device

    # История сгенерированных токенов (копия сида для декодирования)
    generated_tokens_list = list(start_seed)
    current_sequence_ids = torch.tensor([start_seed], dtype=torch.long, device=device)  # (1, L)

    print(f"   Начальная последовательность (Seed): {tokenizer.decode_ids(start_seed)!r}")

    with torch.no_grad():
        for _ in range(max_length):
            # 1. Forward Pass: прогноз следующего токена.
            #    Каузальная маска строится автоматически внутри SelfAttention.
            logits = model(current_sequence_ids)  # (1, L, V)

            # 2. Берем логиты последнего элемента последовательности
            next_token_logits = logits[:, -1, :]  # (1, V)

            # 3. Softmax -> вероятности и выбор следующего токена (Greedy Search)
            probabilities = torch.softmax(next_token_logits, dim=-1)
            predicted_token_index = torch.argmax(probabilities, dim=-1).item()

            # 4. Добавляем предсказанный токен в историю
            generated_tokens_list.append(predicted_token_index)
            new_token = torch.tensor([[predicted_token_index]], dtype=torch.long, device=device)
            current_sequence_ids = torch.cat([current_sequence_ids, new_token], dim=1)

    return tokenizer.decode_ids(generated_tokens_list)
