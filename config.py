"""Runtime settings loaded from the environment / .env file."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()


def _parse_admin_ids(raw: str) -> list[int]:
    ids: list[int] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
    return ids


ADMIN_IDS = _parse_admin_ids(
    os.getenv("ADMIN_IDS", os.getenv("ADMIN_ID", os.getenv("ADMINS", "")))
)
ADMIN_ID = ADMIN_IDS[0] if ADMIN_IDS else 0

DUEL_TIMEZONE = os.getenv("DUEL_TIMEZONE", "Europe/Moscow")
DICK_STEAL_CHANCE = float(os.getenv("DICK_STEAL_CHANCE", "0.2"))
DICK_STEAL_CHANCE_PER_WIN = float(os.getenv("DICK_STEAL_CHANCE_PER_WIN", "0.01"))
DIG_FIND_CHANCE = float(os.getenv("DIG_FIND_CHANCE", "0.15"))
