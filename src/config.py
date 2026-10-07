# ==========================================
# CONFIG — Telegram Bot + Game Config
# ==========================================

import os

# --- Bot ---
TOKEN = os.getenv("TOKEN", "7750125763:AAFjHGXvA9v3EHp1L-OyB1280w6vP7pfnHU")

# --- Rarity colors ---
RARITY_COLORS = {
    "common":    "⬜",
    "uncommon":  "🟩",
    "rare":      "🟦",
    "epic":      "🟪",
    "legendary": "🟨",
    "mythic":    "🟥",
}

# --- Locations ---
LOCATIONS = {
    1: {
        "key": "dark_forest",
        "name": "🌲 Охуенно тёмный лес",
        "emoji": "🌲",
        "min_level": 1,
        "duration": 300,  # 5 минут
        "description": (
            "В 🌲Охуенно Тёмном Лесу растёт особый вид сосны — 🌲Pinus Absurdus. "
            "Её шишки не падают вниз, а выстреливают, попадая прямо в те места, "
            "которые гномы берегут больше всего. 🌲👑Хвойный Барон — это древний дух леса, "
            "принявший облик гигантского, покрытого мхом и лишайниками гуманоида, у которого "
            "вместо головы — огромная, пульсирующая смоляная елда. Может спиздить ваши портки "
            "или другую случайную вещь из инвентаря."
        ),
        "boss_name": "🌲👑 Хвойный Барон",
        "boss_chance": 5,
        "drops": [
            {"item_key": "pine_needle",    "name": "🌿 Хвоя",          "chance": 60, "rarity": "common",    "desc": "Обычная сосновая хвоя."},
            {"item_key": "wood",           "name": "🪵 Древесина",     "chance": 45, "rarity": "common",    "desc": "Кусок гнилой доски. Можно сделать дубину или употребить как растопку."},
            {"item_key": "pine_cone",      "name": "🌰 Шишка",          "chance": 35, "rarity": "uncommon",  "desc": "Шишка Хвойного Барона. Ещё пульсирует."},
            {"item_key": "spiderweb",      "name": "🕸️ Паутина",        "chance": 20, "rarity": "uncommon",  "desc": "Липкая, вонючая, с останками мух. Крафт-материал."},
            {"item_key": "pet_egg",        "name": "🥚 Яйцо питомца",   "chance": 5,  "rarity": "rare",      "desc": "Из него может вылупиться что-то полезное."},
            {"item_key": "baron_resin",    "name": "👑 Смола Барона",   "chance": 2,  "rarity": "epic",      "desc": "Редкий ресурс. Используется для крафта брони."},
        ],
    },
    # Заглушки
    2: {
        "key": "abandoned_mine",
        "name": "⛏️ Заброшенная шахта",
        "emoji": "⛏️",
        "min_level": 3,
        "duration": 600,  # 10 минут
        "description": "...",
        "boss_name": "💀 Костяной Копальщик",
        "boss_chance": 3,
        "drops": [],
    },
    3: {
        "key": "mountain_cave",
        "name": "🦴 Логово горного уебана",
        "emoji": "🦴",
        "min_level": 5,
        "duration": 900,  # 15 минут
        "description": "...",
        "boss_name": "🩸 Кровавый Ультрауебан",
        "boss_chance": 2,
        "drops": [],
    },
}

# --- Combat / Rewards ---
BASE_XP_PER_EXPEDITION = 25
BOSS_XP_BONUS = 100
DROP_ROLLS = 3  # сколько раз роллить лут за экспедицию
LOOT_CHANCE_MULTIPLIER = 1.0  # можно будет баффать