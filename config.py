"""Конфигурационный файл для проекта LLM. Хранит все константы, пути и гиперпараметры."""

import os

# --- Пути к данным и логам ---
DATA_DIR = "data"
TRAINING_FILE = os.path.join(DATA_DIR, "story.txt")  # Абсолютный путь для чтения
LOG_DIR = "training_logs"  # Директория для сохранения логов

# --- Гиперпараметры обучения (Training Hyperparameters) ---
TRAINING_EPOCHS: int = 15
BATCH_SIZE: int = 32
SEQ_LEN: int = 64  # Длина последовательности (включая целевой токен)
LEARNING_RATE: float = 0.001

# --- Гиперпараметры модели и токенизатора ---
MAX_VOCAB_SIZE: int = 256  # Максимальный размер словаря символов
EMBEDDING_DIM: int = 64  # Размерность эмбеддингов (d_model)
NUM_HEADS: int = 8       # Количество голов внимания (в SelfAttention)

# --- Гиперпараметры генерации (Inference Hyperparameters) ---
GENERATION_MAX_TOKENS: int = 40  # Желаемая длина текста в токенах
SEED_LENGTH: int = 30            # Сколько символов использовать как стартовый сид
VOCAB_SIZE: int = MAX_VOCAB_SIZE # Для совместимости с main_app.py

# Если потребуется, можно добавить константы для спец-токенов (например, <EOS> token ID)
PAD_TOKEN_ID = 0  # Предполагаем, что токен с индексом 0 - это PAD/UNK

__all__ = [
    'DATA_DIR', 'TRAINING_FILE', 'LOG_DIR',
    'TRAINING_EPOCHS', 'BATCH_SIZE', 'SEQ_LEN', 'LEARNING_RATE',
    'MAX_VOCAB_SIZE', 'EMBEDDING_DIM', 'NUM_HEADS',
    'GENERATION_MAX_TOKENS', 'SEED_LENGTH', 'PAD_TOKEN_ID',
    'VOCAB_SIZE'  # Для совместимости с main_app.py
]