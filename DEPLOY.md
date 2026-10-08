# 🚀 Деплой на хостинг (шаг за шагом)

## 1. Зайди на сервер хостинга

```bash
ssh user@your-hosting-ip
```

## 2. Установи Docker (если нет)

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install docker.io docker-compose -y

# Добавь себя в группу docker (чтобы не писать sudo каждый раз)
sudo usermod -aG docker $USER
# Перелогинься или:
newgrp docker
```

## 3. Склонируй репозиторий

```bash
git clone https://github.com/draggsconnor/huekrads_bot.git
cd huekrads_bot
```

## 4. Создай `.env` файл

```bash
cp .env.example .env
nano .env
```

Заполни:
```env
BOT_TOKEN=твой_токен_от_BotFather
ADMIN_ID=твой_telegram_id
```

## 5. Собери Docker-образ

```bash
docker build -t huekrads_bot .
```

## 6. Запусти контейнер

```bash
docker run -d \
  --name huekrads \
  --restart unless-stopped \
  -v $(pwd)/data:/app/data \
  --env-file .env \
  huekrads_bot
```

## 7. Проверь, что работает

```bash
# Логи
docker logs -f huekrads

# Статус
docker ps

# Healthcheck
docker inspect --format='{{.State.Health.Status}}' huekrads
```

## 8. Обновление бота (когда выйдет новая версия)

```bash
cd huekrads_bot
git pull
docker build -t huekrads_bot .
docker stop huekrads
docker rm huekrads
docker run -d \
  --name huekrads \
  --restart unless-stopped \
  -v $(pwd)/data:/app/data \
  --env-file .env \
  huekrads_bot
```

## ⚡ Альтернатива: Docker Compose

Если хочешь проще — используй `docker-compose.yml` (создай в папке проекта):

```yaml
version: '3.8'
services:
  bot:
    build: .
    container_name: huekrads
    restart: unless-stopped
    env_file: .env
    volumes:
      - ./data:/app/data
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"]
      interval: 30s
      timeout: 10s
      retries: 3
```

Запуск:
```bash
docker-compose up -d --build
```

## 🛠 Полезные команды

```bash
# Остановить
docker stop huekrads

# Перезапустить
docker restart huekrads

# Удалить контейнер (данные в data/ останутся)
docker rm huekrads

# Зайти внутрь контейнера
docker exec -it huekrads sh

# Размер образа
docker images huekrads_bot
```

## 📁 Где хранятся данные

Все данные бота сохраняются в папке `./data/` на сервере (не внутри контейнера). При обновлении контейнера данные не пропадают.