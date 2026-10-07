"""Конфигурация бота"""

import os
from dotenv import load_dotenv

load_dotenv()

# Основные настройки
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN must be set!")

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

# Папки и пути
DATA_DIR = "data"
PLAYERS_FILE = os.path.join(DATA_DIR, "players.json")

# Начальные параметры игрока
INITIAL_STATS = {
    "hp": 100,
    "max_hp": 100,
    "attack": 10,
    "defense": 5,
    "level": 1,
    "xp": 0,
    "xp_to_next": 100,
    "gold": 50,
}

# Боевые константы
DUNGEON_REWARD_XP = 30
DUNGEON_REWARD_GOLD = 20

# Пределы экспедиции
EXPEDITION_MAX_DURATION_MINUTES = 1440  # 24 часа