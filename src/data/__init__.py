"""Модуль данных (DataLoader): Ответственен за все, что связано с входящими данными."""
from torch.utils.data import Dataset
# Импортируем наш новый модуль токенизатора
from src.tokenizer import Tokenizer 

class DataLoader(Dataset):
    def __init__(self, dataset_path: str, tokenizer: Tokenizer):
        """Инициализирует загрузчик данных."""
        print(f"⚙️ Data Loader инициализирован для чтения из: {dataset_path}")
        self.tokenizer = tokenizer # Принимаем готовый экземпляр токенизатора
        self.data_path = dataset_path

    def __len__(self):
        """Возвращает общее количество элементов в датасете (в реальной жизни - число примеров)."""
        # TODO: В реальном коде нужно считать количество примеров из файла.
        return 10 # Заглушка для теста

    def __getitem__(self, idx):
        """Извлекает данные по индексу (один пример) с использованием токенизатора."""
        print(f"Data loading dummy batch for index {idx}...")
        # Используем токенизатор для преобразования сырых данных.
        dummy_text = "This is the sample text content." 
        encoded_ids = self.tokenizer.encode_text(dummy_text)
        return {"input_ids": encoded_ids, "attention_mask": [1] * len(encoded_ids)} # Возвращаем токены