"""
Питомцы-хуекрабы: вылупление из яиц и перки в экспедиционных боях.

Реестр видов — data/adventure/pets.json (весы вылупления, перки, колорит).
Один активный питомец в player_data под ключом "pet"; повторное вылупление
его заменяет. Перк-снапшот боя делает handlers/battle.py.
"""

import json
import logging
import os
import random
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from handlers.adventure import (
    _EXPEDITION_LOCK,
    _ensure_player_defaults,
    _player_key,
    get_player,
    load_player_data,
    save_player_data,
    PET_EGG_NAME,
)
from handlers.utils import schedule_self_delete
from text_resources import get_text, get_text_mapping

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PETS_PATH = os.getenv("PETS_PATH", os.path.join(BASE_DIR, "data", "adventure", "pets.json"))


def load_pets() -> dict[str, Any]:
    with open(PETS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def roll_pet() -> dict[str, Any]:
    """Взвешенный выбор вида краба из реестра."""
    crabs = (load_pets().get("crabs") or {})
    entries = [
        (crab_id, crab, float(crab.get("weight", 0) or 0))
        for crab_id, crab in crabs.items()
    ]
    total = sum(weight for *_, weight in entries)
    if total <= 0:
        logger.error("Регистр питомцев пуст или без весов")
        raise ValueError("pets.json: нечего вылуплять")

    rand = random.random() * total
    cumulative = 0.0
    for crab_id, crab, weight in entries:
        cumulative += weight
        if rand <= cumulative:
            return {**crab, "id": crab_id}
    last_id, last_crab, _ = entries[-1]
    return {**last_crab, "id": last_id}


def pet_xp_for_next_level(level: int) -> int:
    """XP до следующего уровня хуекраба (50 на уровень).

    Что именно растёт с уровнем краба — Пока не согласовано; уровень —
    просто счётчик боевого стажа.
    """
    return 50 * level


def perk_lines(perks: dict[str, Any]) -> list[str]:
    """Строки описания перков для карточки вылупления."""
    names = get_text_mapping("pets.perk_names")
    lines = []
    for key, value in (perks or {}).items():
        pname = names.get(key)
        if not pname:
            continue
        if key in ("scavenger", "greedy", "berserk"):
            detail = get_text("pets.perk_chance", pct=int(value * 100))
        else:
            detail = ""
        lines.append(get_text("pets.perk_line", name=pname, detail=detail))
    return lines


def render_huecrab_card(player: dict[str, Any]) -> str:
    """Карточка питомца для /adventure_huecrab (описание, перки, уровень)."""
    pet = player.get("pet")
    if not isinstance(pet, dict) or not pet.get("name"):
        return get_text("pets.card_missing")

    lines = [pet.get("name")]
    registry_entry = (load_pets().get("crabs") or {}).get(pet.get("id")) or {}
    flavor = registry_entry.get("flavor")
    if flavor:
        lines.append(f"<i>{flavor}</i>")

    level = int(pet.get("level", 1))
    xp = int(pet.get("xp", 0))
    lines.append("")
    lines.append(get_text(
        "pets.card_level",
        level=level, xp=xp, to_next=pet_xp_for_next_level(level),
    ))
    lines.extend(perk_lines(pet.get("perks") or {}))
    return "\n".join(lines)


async def adventure_huecrab_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/adventure_huecrab — карточка активного хуекраба."""
    user = update.effective_user
    if not user or not update.effective_message:
        return

    # Уборка: команда и ответ бота живут минуту.
    await schedule_self_delete(context, update.effective_message)

    player = get_player(user.id)
    message = await update.effective_message.reply_text(
        render_huecrab_card(player),
        parse_mode=ParseMode.HTML,
    )
    await schedule_self_delete(context, message)


def hatch_pet(user_id: int) -> dict[str, Any]:
    """Атомарно вылупить краба из одного 🥚 яйца питомца.

    Яйцо потребляется из инвентаря; старый питомец (если был) заменяется —
    «уползает на волю». Возвращает {"event": "empty"} если яиц нет.
    """
    with _EXPEDITION_LOCK:
        data = load_player_data()
        player = data.get(_player_key(user_id))
        if not isinstance(player, dict):
            return {"event": "empty"}
        _ensure_player_defaults(player)

        inventory = player.get("inventory", [])
        egg_index = None
        for idx, item in enumerate(inventory):
            if item.get("name") == PET_EGG_NAME:
                egg_index = idx
                break
        if egg_index is None:
            return {"event": "empty"}

        old_pet = player.get("pet") if isinstance(player.get("pet"), dict) else None
        species = roll_pet()
        del inventory[egg_index]

        player["pet"] = {
            "id": species["id"],
            "name": species.get("name"),
            "perks": dict(species.get("perks") or {}),
            "level": 1,
            "xp": 0,
        }
        save_player_data(data)

        eggs_left = sum(1 for item in inventory if item.get("name") == PET_EGG_NAME)
        return {
            "event": "hatched",
            "pet_name": species.get("name"),
            "flavor": species.get("flavor"),
            "perks": species.get("perks") or {},
            "perk_lines": perk_lines(species.get("perks") or {}),
            "replaced": old_pet.get("name") if old_pet else None,
            "eggs_left": eggs_left,
        }
