"""Модуль токенизатора: Абстрагирует сложную логику работы со словарями и токенами.

Реализация CharacterTokenizer — базовый токенизатор на уровне символов для обучения простой LLM.
Поддерживает:
- Построение словаря из текста (максимальный размер 256 символов)
- Кодирование текста в последовательность индексов токенов
- Декодирование индексов обратно в текст

Это реализация на уровне символов, как требуется для учебного проекта: модель обучается ТОЛЬКО на data/story.txt.
"""

import torch
from collections import Counter


class CharacterTokenizer:
    """
    Токенизатор на уровне символов (character-level tokenizer).
    
    Для обучаемой модели LLM на story.txt используется токенизация на уровне символов,
    так как:
    1. Это позволяет обрабатывать любой текст без предварительного обучения
    2. В словаре только уникальные символы из story.txt (макс. 256)
    3. Модель учится предсказывать следующий символ на основе контекста
    
    Пример работы:
        text = "Привет"
        tokenizer = CharacterTokenizer()
        vocab = tokenizer.build_vocab(text, max_size=256)  # {'П':0, 'р':1, 'и':2, ...}
        ids = tokenizer.encode_text(text)                  # [0, 1, 1, 3, 5, 4]
        text_reconstructed = tokenizer.decode_ids(ids)    # "Привет"
    """

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
        2. Берём до max_vocab_size наиболее часто встречающихся символов
        3. Назначаем индексам, начиная с 0
        
        Args:
            text: Полный текст из story.txt
            
        Returns:
            Словарь {символ: индекс}
        """
        # Считаем частоту каждого символа
        freq = Counter(text)
        
        # Берем до max_vocab_size наиболее часто встречающихся символов
        most_common_symbols = freq.most_common(self.max_vocab_size)
        
        # Создаём словарь и обратный словарь
        self.vocab = {sym: idx for idx, (sym, _) in enumerate(most_common_symbols)}
        self.inv_vocab = {idx: sym for sym, idx in self.vocab.items()}
        
        print(f"📚 Словарь токенов сформирован. Размер словаря: {len(self.vocab)}/{self.max_vocab_size}")
        print(f"   Пример символов: {list(self.vocab.keys())[:10]}")
        
        return self.vocab
    
    def encode_text(self, text: str) -> list[int]:
        """
        Преобразует сырой текст в последовательность индексов токенов.
        
        Для каждого символа из входного текста ищем его индекс в словаре.
        Если символ не найден (например, редкий символ), заменяем на <UNK>.
        
        Args:
            text: Полный текст для токенизации
            
        Returns:
            Список индексов токенов [idx1, idx2, ..., idxN]
            
        Пример:
            input_text  = "Привет"
            output_ids  = [0, 1, 1, 3, 5, 4]
        """
        # Заменяем неизвестные символы на <UNK>
        unk_token = "<unk>"
        
        if unk_token not in self.vocab:
            # Если <unk> не в словаре, добавляем его с индексом 0
            self.vocab[unk_token] = 0
            self.inv_vocab[0] = unk_token
        
        unk_index = self.vocab.get(unk_token)
        
        ids = []
        for char in text:
            if char in self.vocab:
                ids.append(self.vocab[char])
            else:
                ids.append(unk_index)  # Заменяем неизвестный символ на <UNK>
        
        return ids
    
    def decode_ids(self, ids: list[int] | torch.Tensor) -> str:
        """
        Восстанавливает текст из последовательности индексов токенов.
        
        Превращает каждый индекс токена обратно в символ с использованием обратного словаря.
        
        Args:
            ids: Последовательность индексов токенов
            
        Returns:
            Восстановленный текстовый строка
        """
        if isinstance(ids, torch.Tensor):
            ids = ids.tolist()
        
        # Заменяем неизвестные индексы на <unk>
        unk_index = self.vocab.get("<unk>", 0)
        
        decoded_chars = []
        for idx in ids:
            char = self.inv_vocab.get(idx, "?")
            decoded_chars.append(char)
        
        return "".join(decoded_chars)
    
    def get_vocab_size(self) -> int:
        """Возвращает размер словаря."""
        return len(self.vocab)


# Экспорт класса CharacterTokenizer для использования в других модулях
__all__ = ["CharacterTokenizer"]