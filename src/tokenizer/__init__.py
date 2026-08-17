"""Модуль токенизатора: Абстрагирует сложную логику работы со словарями и токенами.

Реэкспорт публичных классов из подмодулей:
    - CharacterTokenizer (src/tokenizer/character_tokenizer.py)
"""
from src.tokenizer.character_tokenizer import CharacterTokenizer

__all__ = ["CharacterTokenizer"]
