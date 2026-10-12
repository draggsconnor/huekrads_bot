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
import uuid
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
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
from handlers.utils import (
    DEFAULT_DELETE_DELAY,
    redirect_self_delete,
    schedule_self_delete,
)
from text_resources import get_text, get_text_mapping

logger = logging.getLogger(__name__)

# Окно интерактива в сарае (смена крабов), как у пикера поедания.
SARAY_SESSION_SECONDS = 300

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


def name_pet(user_id: int, pet_name: str) -> dict[str, Any]:
    """Назвать питомца — ровно один раз (CAS по pet.renamed)."""
    pet_name = " ".join((pet_name or "").split())[:24]
    if not pet_name:
        return {"event": "empty_name"}

    with _EXPEDITION_LOCK:
        data = load_player_data()
        player = data.get(_player_key(user_id))
        if not isinstance(player, dict):
            return {"event": "empty"}
        _ensure_player_defaults(player)

        pet = player.get("pet")
        if not isinstance(pet, dict) or not pet.get("name"):
            return {"event": "empty"}
        if pet.get("renamed"):
            return {"event": "already_named", "name": pet.get("name")}

        pet["name"] = pet_name
        pet["renamed"] = True
        save_player_data(data)
        return {"event": "named", "name": pet_name}


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


async def adventure_huecrab_name_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/adventure_huecrab_name <имя> — назвать хуекраба (один раз)."""
    from html import escape

    user = update.effective_user
    if not user or not update.effective_message:
        return

    # Уборка: команда и ответ бота живут минуту.
    await schedule_self_delete(context, update.effective_message)

    args = " ".join(context.args or []).strip()
    if not args:
        await update.effective_message.reply_text(
            get_text("pets.name_usage"),
            parse_mode=ParseMode.HTML,
        )
        return

    result = name_pet(user.id, args)
    if result.get("event") == "empty":
        text = get_text("pets.name_need_pet")
    elif result.get("event") == "already_named":
        text = get_text("pets.name_already", name=escape(result.get("name", "")))
    elif result.get("event") == "named":
        text = get_text("pets.name_set", name=escape(result["name"]))
    else:  # empty_name — не должно случаться (args непусты)
        text = get_text("pets.name_usage")
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)


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
        reply_markup=_huecrab_card_keyboard(user.id, player),
    )
    await schedule_self_delete(context, message)


def _crab_uid() -> str:
    return uuid.uuid4().hex[:8]


def _ensure_crab_uid(pet: dict[str, Any]) -> str:
    """У каждого краба — свои метки/имя; uid нужен адресным кнопкам."""
    if not pet.get("uid"):
        pet["uid"] = _crab_uid()
    return pet["uid"]


def move_to_saray(user_id: int, uid: str) -> dict[str, Any]:
    """Вне боя: сменить экипированного краба на обитателя сарая.

    Возвращает {"event": "swapped", new, old}, {"event": "battle"} —
    во время стычки смена запрещена, или {"event": "empty"}.
    """
    with _EXPEDITION_LOCK:
        data = load_player_data()
        player = data.get(_player_key(user_id))
        if not isinstance(player, dict):
            return {"event": "empty"}
        _ensure_player_defaults(player)

        if isinstance(player.get("active_battle"), dict):
            return {"event": "battle"}

        pet = player.get("pet")
        saray = [c for c in (player.get("saray") or []) if isinstance(c, dict) and c.get("name")]
        chosen = next((c for c in saray if c.get("uid") == uid), None)
        if not isinstance(pet, dict) or not pet.get("name") or chosen is None:
            return {"event": "empty"}

        _ensure_crab_uid(pet)
        player["saray"] = [c for c in saray if c.get("uid") != uid] + [pet]
        player["pet"] = chosen
        save_player_data(data)
        return {"event": "swapped", "new": chosen.get("name"), "old": pet.get("name")}


def _huecrab_card_keyboard(user_id: int, player: dict[str, Any]):
    """Карточка питомца: кнопка имени у неназванного краба."""
    pet = player.get("pet")
    if isinstance(pet, dict) and pet.get("name") and not pet.get("renamed"):
        return InlineKeyboardMarkup([[
            InlineKeyboardButton(
                get_text("pets.card_rename_button"),
                callback_data=f"hcrab_{user_id}_rename",
            )
        ]])
    return None


def _pet_brief(pet: dict[str, Any]) -> str:
    """Краткая строка краба: имя (ур.), главная роль."""
    perk_names = get_text_mapping("pets.perk_names")
    perks = pet.get("perks") or {}
    role = next((perk_names[key] for key in perks if key in perk_names), "")
    label = f"{pet.get('name')} (ур. {int(pet.get('level', 1))})"
    if role:
        label += f" · {role}"
    return label


def _render_saray(player: dict[str, Any]) -> str:
    lines = [get_text("pets.saray_title"), ""]

    pet = player.get("pet")
    if isinstance(pet, dict) and pet.get("name"):
        lines.append(get_text("pets.saray_active", brief=_pet_brief(pet)))

    saray = [c for c in (player.get("saray") or []) if isinstance(c, dict) and c.get("name")]
    if saray:
        lines.append(get_text("pets.saray_head"))
        for crab in saray:
            lines.append(f"· {_pet_brief(crab)}")
    else:
        lines.append(get_text("pets.saray_empty"))
    return "\n".join(lines)


def _saray_keyboard(user_id: int, player: dict[str, Any]):
    saray = [c for c in (player.get("saray") or []) if isinstance(c, dict) and c.get("name")]
    if not saray:
        return None
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            get_text("pets.saray_pick_button"),
            callback_data=f"saray_{user_id}_open",
        )
    ]])


def _saray_pick_keyboard(user_id: int, player: dict[str, Any]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            _pet_brief(crab),
            callback_data=f"saray_{user_id}_swap_{crab.get('uid')}",
        )]
        for crab in player.get("saray", [])
        if isinstance(crab, dict) and crab.get("name") and crab.get("uid")
    ]
    rows.append([InlineKeyboardButton(
        get_text("pets.saray_cancel"),
        callback_data=f"saray_{user_id}_cancel",
    )])
    return InlineKeyboardMarkup(rows)


def _migrated_player(user_id: int) -> dict[str, Any]:
    """Подтягиваем uid-метки крабов (адресность кнопок сараев)."""
    with _EXPEDITION_LOCK:
        data = load_player_data()
        player = data.get(_player_key(user_id))
        if not isinstance(player, dict):
            {"pet": None, "saray": []}  # вернём дефолт ниже
        else:
            _ensure_player_defaults(player)
            pet = player.get("pet")
            if isinstance(pet, dict) and pet.get("name"):
                _ensure_crab_uid(pet)
            saray = []
            for crab in (player.get("saray") or []):
                if isinstance(crab, dict) and crab.get("name"):
                    _ensure_crab_uid(crab)
                    saray.append(crab)
            player["saray"] = saray
            save_player_data(data)
            return player
    return {"pet": None, "saray": []}


def _saray_message_args(update: Update) -> tuple[int, int] | None:
    message = update.callback_query.message if update.callback_query else None
    return (message.chat_id, message.message_id) if message else None


async def saray_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Колбэки сарая: saray_{owner}_{open|cancel} и saray_{owner}_swap_{uid}."""
    query = update.callback_query
    if query is None:
        return

    parts = (query.data or "").split("_")
    user = update.effective_user
    if user is None or len(parts) < 3 or parts[0] != "saray":
        await query.answer()
        return
    try:
        owner_id = int(parts[1])
    except ValueError:
        await query.answer()
        return
    if user.id != owner_id:
        await query.answer(get_text("pets.saray_not_your"))
        return

    action = parts[2]
    player = get_player(user.id)
    message_args = _saray_message_args(update)

    if action == "open":
        if message_args:
            await redirect_self_delete(context, *message_args, SARAY_SESSION_SECONDS)
        saray = [c for c in (player.get("saray") or []) if isinstance(c, dict) and c.get("name")]
        if not saray:
            await query.answer(get_text("pets.saray_nothing"))
            return
        await query.edit_message_text(
            text=get_text("pets.saray_pick_title"),
            parse_mode=ParseMode.HTML,
            reply_markup=_saray_pick_keyboard(user.id, player),
        )
        await query.answer()
        return

    if action == "cancel":
        await query.edit_message_text(
            text=_render_saray(player),
            parse_mode=ParseMode.HTML,
            reply_markup=_saray_keyboard(user.id, player),
        )
        if message_args:
            await redirect_self_delete(context, *message_args, DEFAULT_DELETE_DELAY)
        await query.answer()
        return

    if action == "swap" and len(parts) == 4:
        result = move_to_saray(user.id, parts[3])
        if result.get("event") == "battle":
            await query.answer(get_text("pets.saray_battle_busy"))
            return
        if result.get("event") != "swapped":
            await query.answer(get_text("pets.saray_nothing"))
            return

        player = get_player(user.id)
        text = get_text("pets.saray_picked", new=result["new"], old=result["old"])
        text += "\n\n" + _render_saray(player)
        if message_args:
            await redirect_self_delete(context, *message_args, SARAY_SESSION_SECONDS)
        await query.edit_message_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=_saray_keyboard(user.id, player),
        )
        await query.answer()
        return

    await query.answer()


PENDING_RENAME_KEY = "pet_rename_pending"


async def huecrab_card_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопка «ДАТЬ ИМЯ» в карточке — тумблер ожидания имени текстом."""
    query = update.callback_query
    if query is None:
        return

    parts = (query.data or "").split("_")  # hcrab_{owner}_rename
    user = update.effective_user
    if user is None or len(parts) != 3 or parts[0] != "hcrab":
        await query.answer()
        return
    try:
        owner_id = int(parts[1])
    except ValueError:
        await query.answer()
        return
    if user.id != owner_id:
        await query.answer(get_text("pets.saray_not_your"))
        return

    player = get_player(user.id)
    pet = player.get("pet") if isinstance(player, dict) else None
    if not isinstance(pet, dict) or not pet.get("name") or pet.get("renamed"):
        await query.answer(get_text("pets.name_need_pet"))
        return

    if context.user_data.get(PENDING_RENAME_KEY):
        context.user_data.pop(PENDING_RENAME_KEY, None)
        await query.answer(get_text("pets.rename_cancel_toast"))
        return

    context.user_data[PENDING_RENAME_KEY] = query.message.chat_id if query.message else user.id
    await query.answer(get_text("pets.rename_pending_toast"))


async def pet_name_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Если бот ждёт имя питомца от пользователя — следующий текст это имя."""
    from html import escape

    message = update.effective_message
    user = update.effective_user
    if not message or not message.text:
        return
    if not user:
        return

    pending = (context.user_data or {}).get(PENDING_RENAME_KEY)
    if not pending or pending != message.chat_id:
        return  # обычное сообщение мимо режима именования

    context.user_data.pop(PENDING_RENAME_KEY, None)
    result = name_pet(user.id, message.text)

    # Текст-ввод имени — мусор в чате: удаляем (нужны права, иначе останется).
    try:
        await context.bot.delete_message(
            chat_id=message.chat_id, message_id=message.message_id,
        )
    except Exception:
        pass

    if result.get("event") == "named":
        await message.reply_text(
            get_text("pets.name_set", name=escape(result["name"])),
            parse_mode=ParseMode.HTML,
        )
    elif result.get("event") == "already_named":
        await message.reply_text(
            get_text("pets.name_already", name=escape(result.get("name", ""))),
            parse_mode=ParseMode.HTML,
        )
    elif result.get("event") == "empty_name":
        await message.reply_text(get_text("pets.name_usage"))


async def adventure_saray_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/adventure_saray — сарай хуекрабов: кто с гномом, кто в сарае."""
    user = update.effective_user
    if not user or not update.effective_message:
        return

    # Уборка: команда и ответ бота живут минуту.
    await schedule_self_delete(context, update.effective_message)

    player = _migrated_player(user.id)
    message = await update.effective_message.reply_text(
        _render_saray(player),
        parse_mode=ParseMode.HTML,
        reply_markup=_saray_keyboard(user.id, player),
    )
    await schedule_self_delete(context, message)
def hatch_pet(user_id: int) -> dict[str, Any]:
    """Атомарно вылупить краба из одного 🥚 яйца питомца.

    Яйцо потребляется из инвентаря; старый питомец (если был) отправляется
    в сарай. Возвращает {"event": "empty"} если яиц нет.
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

        if old_pet:
            # Прежний хуекраб переезжает в сарай (уровень/XP сохраняются).
            _ensure_crab_uid(old_pet)
            player.setdefault("saray", []).append(old_pet)

        player["pet"] = {
            "id": species["id"],
            "name": species.get("name"),
            "perks": dict(species.get("perks") or {}),
            "level": 1,
            "xp": 0,
            "uid": _crab_uid(),
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
