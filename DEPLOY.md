# Деплой на хостинг

Рабочая точка входа: `bot.py` (экспедиции). Данные игроков пишутся в `persist/`, игровые JSON локаций остаются в образе.

## 1. На сервере

```bash
git clone https://github.com/draggsconnor/huekrads_bot.git
cd huekrads_bot
cp .env.example .env
nano .env
```

В `.env` обязательно:

```env
BOT_TOKEN=токен_от_BotFather
ADMIN_IDS=твой_telegram_id
```

## 2. Запуск через Docker Compose

```bash
docker compose up -d --build
docker compose logs -f
```

В Telegram: `/start` или `/adventure`.

## 3. Обновление

```bash
cd huekrads_bot
git pull
docker compose up -d --build
```

Папка `persist/` на хосте не трогается — прогресс игроков сохраняется.

## Без Compose

```bash
docker build -t huekrads_bot .
docker run -d --name huekrads --restart unless-stopped \
  --env-file .env \
  -v "$(pwd)/persist:/app/persist" \
  huekrads_bot
```
