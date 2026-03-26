FROM python:3.11-slim

WORKDIR /app

# Устанавливаем системные зависимости для компиляции Rust/C++ пакетов
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libc6-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Сначала обновляем pip, потом ставим зависимости
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

# Запуск через python, а не через bash-скрипт (так надежнее для логов)
CMD ["python", "main.py"]