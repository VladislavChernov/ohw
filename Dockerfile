# Используем официальный образ Python для разработки
FROM python:3.10-slim

# Устанавливаем рабочую директорию в контейнере
WORKDIR /app

# Копируем файл с зависимостями и устанавливаем их
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем остальной код приложения
COPY src/ ./src

# По умолчанию запускаем приложение
CMD ["python", "src/main.py"]