"""Конфигурационный файл для проекта LLM. Хранит все константы, пути и гиперпараметры."""

# --- Пути к данным и логам (Унифицировано) ---
DATA_DIR = "data"           # Основная директория с данными (story.txt, pdfs, docx)
LOG_DIR = "training_logs"   # Директория для сохранения всех логов обучения

# --- Поддерживаемые расширения файлов (для сканера и диспетчера) ---
SUPPORTED_EXTENSIONS: list[str] = [".txt", ".pdf", ".docx"]

# --- Гиперпараметры обучения (Training Hyperparameters) ---
TRAINING_EPOCHS: int = 15
BATCH_SIZE: int = 32
SEQ_LEN: int = 64  # Длина обучающей последовательности (вход и сдвинутый target)
LEARNING_RATE: float = 0.001

# --- Гиперпараметры модели и токенизатора ---
MAX_VOCAB_SIZE: int = 256  # Максимальный размер словаря символов (включая <unk>)
EMBEDDING_DIM: int = 64  # Размерность эмбеддингов (d_model)
NUM_HEADS: int = 8       # Количество голов внимания (в SelfAttention)
DROPOUT_RATE: float = 0.1  # Коэффициент dropout в SelfAttention

# --- Гиперпараметры генерации (Inference Hyperparameters) ---
GENERATION_MAX_TOKENS: int = 40  # Сколько новых токенов генерировать после сида
SEED_LENGTH: int = 30            # Сколько символов промпта использовать как стартовый сид

# Индекс 0 в токенизаторе зарезервирован под <unk>, см. src/tokenizer/__init__.py
UNK_TOKEN_ID: int = 0

__all__ = [
    'DATA_DIR', 'LOG_DIR', 'SUPPORTED_EXTENSIONS',
    'TRAINING_EPOCHS', 'BATCH_SIZE', 'SEQ_LEN', 'LEARNING_RATE',
    'MAX_VOCAB_SIZE', 'EMBEDDING_DIM', 'NUM_HEADS', 'DROPOUT_RATE',
    'GENERATION_MAX_TOKENS', 'SEED_LENGTH', 'UNK_TOKEN_ID',
]
