"""Модуль токенизатора: Абстрагирует сложную логику работы со словарями и токенами.

Реализация CharacterTokenizer — базовый токенизатор на уровне символов для обучения простой LLM.
Поддерживает:
- Построение словаря из текста (максимальный размер 256 символов, индекс 0 резервируется под <unk>)
- Кодирование текста в последовательность индексов токенов
- Декодирование индексов обратно в текст

Это реализация на уровне символов, как требуется для учебного проекта.
"""

import torch
from collections import Counter


class CharacterTokenizer:
    """
    Токенизатор на уровне символов (character-level tokenizer).

    Индекс 0 всегда зарезервирован под специальный токен <unk> (неизвестный символ).
    Остальные индексы (1..max_vocab_size-1) назначаются самым частым символам текста.
    Поэтому размер словаря никогда не превышает max_vocab_size.

    Пример работы:
        text = "Привет"
        tokenizer = CharacterTokenizer()
        vocab = tokenizer.build_vocab(text, max_size=256)  # {'<unk>':0, 'П':1, 'р':2, ...}
        ids = tokenizer.encode_text(text)                  # [1, 2, 2, 4, 6, 5]
        text_reconstructed = tokenizer.decode_ids(ids)     # "Привет"
    """

    UNK_TOKEN = "<unk>"

    def __init__(self, max_vocab_size: int = 256):
        """
        Инициализация токенизатора.

        Args:
            max_vocab_size: Максимальный размер словаря (по умолчанию 256 символов)
        """
        self.max_vocab_size = max_vocab_size
        self.vocab = {}  # symbol -> index
        self.inv_vocab = {}  # index -> symbol

    def build_vocab(self, text: str) -> dict[str, int]:
        """
        Строит словарь токенов из текста.

        Алгоритм:
        1. Считаем частоту каждого символа в тексте
        2. Резервируем индекс 0 под <unk>
        3. Берём до max_vocab_size-1 наиболее часто встречающихся символов
        4. Назначаем им индексы 1..max_vocab_size-1

        Args:
            text: Полный текст корпуса

        Returns:
            Словарь {символ: индекс}
        """
        freq = Counter(text)

        # Оставляем одно место под <unk> (индекс 0)
        most_common_symbols = freq.most_common(self.max_vocab_size - 1)

        self.vocab = {self.UNK_TOKEN: 0}
        self.vocab.update({sym: idx + 1 for idx, (sym, _) in enumerate(most_common_symbols)})
        self.inv_vocab = {idx: sym for sym, idx in self.vocab.items()}

        print(f"Словарь токенов сформирован. Размер словаря: {len(self.vocab)}/{self.max_vocab_size}")
        print(f"   Пример символов: {list(self.vocab.keys())[1:11]}")

        return self.vocab

    def encode_text(self, text: str) -> list[int]:
        """
        Преобразует сырой текст в последовательность индексов токенов.

        Для каждого символа из входного текста ищем его индекс в словаре.
        Если символ не найден (например, редкий символ), заменяем на <unk> (индекс 0).

        Args:
            text: Полный текст для токенизации

        Returns:
            Список индексов токенов [idx1, idx2, ..., idxN]
        """
        return [self.vocab.get(char, 0) for char in text]

    def decode_ids(self, ids: list[int] | torch.Tensor) -> str:
        """
        Восстанавливает текст из последовательности индексов токенов.

        Args:
            ids: Последовательность индексов токенов

        Returns:
            Восстановленный текст
        """
        if isinstance(ids, torch.Tensor):
            ids = ids.tolist()

        return "".join(self.inv_vocab.get(idx, "?") for idx in ids)

    def get_vocab_size(self) -> int:
        """Возвращает размер словаря."""
        return len(self.vocab)


# Экспорт класса CharacterTokenizer для использования в других модулях
__all__ = ["CharacterTokenizer"]