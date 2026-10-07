FROM python:3.11-slim

WORKDIR /app

# Сначала копируем только requirements.txt
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Затем копируем весь остальной код
COPY . .

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "src.main"]