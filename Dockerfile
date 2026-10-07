<<<<<<< HEAD
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
=======
FROM python:3.10-slim

WORKDIR /app

# Сначала копируем только requirements.txt
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Затем копируем весь остальной код
COPY . .

CMD ["python", "bot.py"]
>>>>>>> bd0593c31f44d624d955bae7c70ac12d81e7bf08
