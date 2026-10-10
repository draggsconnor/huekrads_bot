# Журнал разработки Huekrads Bot

> ⚠️ Ранние записи этого журнала описывают предыдущую итерацию проекта
> (AIOGram + SQLAlchemy). Рабочий бот сейчас построен на python-telegram-bot 20.x
> с JSON-хранилищем игроков и SQLite; актуальная точка входа — `bot.py`
> (см. README.md). Каталог `src/` — экспериментальная модульная версия.

## [2025-10-07] Сессия: Инициализация проекта

**Git-репозиторий:** https://github.com/draggsconnor/huekrads_bot

### Что сделано
- ✅ Создана структура проекта на Python с poetry/npm-style организацией (`src/`)
- ✅ Настроен инфраструктурный слой: логирование, конфиг (`python-dotenv`), AIOGram 3.x
- ✅ Реализована система хранения: ORM SQLAlchemy 2.0 + async SQLite (`aiosqlite`), миграции отключены (`create_all`)
- ✅ Полностью разработана игровая механика:
  - Существа: 10 уникальных типов с бонусами к характеристикам
  - Параметры: Сила, Ловкость, Выносливость, Интеллект (D&D-style, 1d6+8, дважды 2 раза)
  - Три класса (Воин, Лучник, Маг) с уникальными способностями
  - Уровневый прогресс: XP → уровни, таланты каждые 2 уровня
  - 10 локаций с уникальными врагами, лутом и временем исследования
  - Крафтинг (рецепты) и торговля с NPC-торговцами
  - Система достижений
  - PvP арена: очередь и бои
  - Лаунчер-меню и навигация по локациям
  - Комбат и Scheduler (модульная архитектура)

### Технический стек
```
Python 3.10+, AIOGram 3.x, SQLAlchemy 2.0 (async), aiosqlite,
python-dotenv, Docker, GitHub Actions (TODO)
```

### Архитектура
```
src/
├── __init__.py      # game_state, secret_token, logger
├── config.py        # Settings (env-переменные, ADMINS)
├── logger.py        # Настройка логирования (консоль + файл)
├── models.py        # ORM-модели (User, LocationVisit, MessageLog)
├── storage.py       # Игровой стейт, кэширование, CRUD, `save_game(user)`
├── combat.py        # Боевая система (пока заглушка/базовый класс)
├── scheduler.py     # Планировщик событий (пока заглушка)
└── handlers.py      # Telegram handlers + WebhookLauncher + launch_menu()
main.py              # Точка входа: argparse (poll/webhook), on_startup
```

### Схема данных (SQLAlchemy ORM)

**Таблица `users`**
| Поле | Тип | Описание |
|------|-----|----------|
| id | Integer PK | Внутренний ID |
| telegram_id | BigInteger, unique | Telegram ID игрока |
| username | String(128) | @username |
| first_name | String(128) | Имя |
| last_name | String(128) | Фамилия |
| language_code | String(10) | Код языка |
| chat_id | BigInteger | ID чата для уведомлений |
| created_at | DateTime | Время регистрации |
| last_activity | DateTime | Последняя активность |

**Таблица `location_visits`** — история исследования локаций
| Поле | Тип | Описание |
|------|-----|----------|
| id | Integer PK | — |
| user_id | Integer FK → users.id | Игрок |
| location | String(50) | Название локации |
| visited_at | DateTime | Время посещения |
| success | Boolean | Успех исследования |

**Таблица `message_logs`** — лог сообщений (для отладки)
| Поле | Тип | Описание |
|------|-----|----------|
| id | Integer PK | — |
| user_id | Integer FK → users.id | Игрок |
| chat_id | BigInteger | Чат |
| message_text | Text Текст сообщения | — |
| is_from_user | Boolean | От пользователя или бота |
| created_at | DateTime | Время |

### Конфигурация (`.env`)
```env
BOT_TOKEN=...
ADMINS=123456789,987654321
USE_WEBHOOK=False
# WEBHOOK_URL=... (если USE_WEBHOOK=True)
DATABASE_URL=sqlite:///huekrads.db
```

### Запуск
```bash
# Локально
poetry install && poetry run python main.py --mode poll

# Docker
docker-compose up --build
```

### Следующие шаги (бэклог)
- [ ] Углубить боевую систему (`src/combat.py`)
- [ ] Реализовать планировщик событий (`src/scheduler.py`)
- [ ] Добавить тесты (pytest)
- [ ] Настроить GitHub Actions для CI/CD
- [ ] Добавить i18n (многоязычность)
- [ ] Реализовать сохранение в PostgreSQL (для продакшена)