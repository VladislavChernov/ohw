"""Модуль для генерации текста (Инференс)."""
import torch
import numpy as np
from src.tokenizer import CharacterTokenizer

def generate_text(model, tokenizer: CharacterTokenizer, start_seed: list[int], max_length: int = 40) -> str | None:
    """
    Генерирует текст авторегрессионным методом на основе заданной начальной последовательности.
    
    Args:
        model: Обученная модель TransformerModel (в режиме evaluation).
        tokenizer: Инициализированный токенизатор.
        start_seed: Начальная последовательность индексов для генерации.
        max_length: Максимальное количество генерируемых токенов.

    Returns:
        Сгенерированная строка текста или None в случае ошибки.
    """
    print("\n⚙️ Начинаем авторегрессионную генерацию...")
    model.eval() # Переводим модель в режим оценки (отключает Dropout)
    device = next(model.parameters()).device

    # История сгенерированных токенов
    current_sequence_ids = torch.tensor([torch.LongTensor(start_seed)]).to(device) 
    generated_tokens_list = start_seed[:] # Копия для декодирования

    print(f"   Начальная последовательность (Seed): {tokenizer.decode_ids(current_sequence_ids)}")

    with torch.no_grad():
        for step in range(max_length):
            # 1. Создание каузальной маски
            # Маска гарантирует, что токен на позиции 'i' видит только токены < i.
            mask = torch.tril(torch.ones((len(current_sequence_ids), len(current_sequence_ids)), dtype=torch.uint8)).to(device)

            # 2. Forward Pass: Прогноз следующего токена
            logits = model(current_sequence_ids, mask) # (1, L, V)
            
            # Получаем логиты для последнего элемента в последовательности (это прогноз на следующий токен)
            next_token_logits = logits[:, -1, :] # Размер: (1, VocabSize)

            # 3. Расчет вероятностей и выбор следующего токена
            probabilities = torch.softmax(next_token_logits, dim=-1)
            
            # Простая стратегия выбора: всегда берем токен с максимальной вероятностью (Greedy Search)
            predicted_token_index = torch.argmax(probabilities, dim=-1).item()

            # 4. Добавление в историю и проверка стоп-условий
            generated_tokens_list.append(predicted_token_index)
            current_sequence_ids = torch.cat([current_sequence_ids, torch.tensor([predicted_token_index], dtype=torch.long).to(device)], dim=1)

            # Проверка на токен конца последовательности (если он в словаре)
            if tokenizer.vocab.get(tokenizer.inv_vocab[0]) == "<EOS>": # Предполагаем, что 0 - это EOS
                break
        
    return tokenizer.decode_ids(generated_tokens_list)