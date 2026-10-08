FROM python:3.11-slim

WORKDIR /app

# Сначала копируем только requirements.txt
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Затем копируем весь код, включая .env (он не в .gitignore для Docker, но есть на хосте)
COPY . .

ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "src.main"]
