FROM python:3.12-slim

# Оптимальні змінні середовища
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Копіюємо requirements і встановлюємо залежності
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Копіюємо весь код
COPY . .

# Щоб Python бачив локальні імпорти (routers, services і т.д.)
ENV PYTHONPATH=/app

# Запуск: слухаємо PORT від Railway (fallback 8080 локально)
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}"]