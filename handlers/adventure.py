"""
Adventure module: turn-based PvE fights at various locations.
Uses:
  data/adventure/locations.json   — location definitions & loot tables
  data/adventure/global_pool.json — global item pool references
  data/adventure/player_data.json — per-player HP / cooldowns / stats
  data/adventure/mobs.json        — mob definitions for encounters
  texts/adventure.yaml            — user-facing texts
"""

import hashlib
import json
import logging
import os
import random
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, CommandHandler
from telegram.constants import ParseMode

from handlers.utils import (
    DEFAULT_DELETE_DELAY,
    is_admin,
    redirect_self_delete,
    schedule_self_delete,
)
from text_resources import get_text, get_text_list

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADVENTURE_DIR = os.path.join(BASE_DIR, "data", "adventure")
# Отбивка по итогу экспедиции живёт дольше остальных ответов — итоги прочитать успеть.
EXPEDITION_RESULT_DELETE_DELAY = 120
# /adventure (меню локаций + команда игрока) — тоже две минуты: выбор
# локации с кнопками долго не застаивается.
ADVENTURE_DELETE_DELAY = 120
PERSIST_DIR = os.path.join(BASE_DIR, "persist")
RECIPES_PATH = os.path.join(ADVENTURE_DIR, "recipes.json")

LOCATIONS_PATH = os.path.join(ADVENTURE_DIR, "locations.json")
GLOBAL_POOL_PATH = os.path.join(ADVENTURE_DIR, "global_pool.json")
PLAYER_DATA_PATH = os.getenv(
    "PLAYER_DATA_PATH",
    os.path.join(PERSIST_DIR, "adventure_player_data.json"),
)
MOBS_PATH = os.path.join(ADVENTURE_DIR, "mobs.json")


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_locations() -> dict[str, Any]:
    return _load_json(LOCATIONS_PATH)


def load_recipes() -> list[dict[str, Any]]:
    """Рецепты кузни (data/adventure/recipes.json)."""
    try:
        return list(_load_json(RECIPES_PATH).get("recipes") or [])
    except FileNotFoundError:
        return []


def load_global_pool() -> dict[str, Any]:
    return _load_json(GLOBAL_POOL_PATH)


def load_mobs() -> dict[str, Any]:
    return _load_json(MOBS_PATH)


def load_player_data() -> dict[str, Any]:
    if not os.path.exists(PLAYER_DATA_PATH):
        return {}
    data = _load_json(PLAYER_DATA_PATH)
    if isinstance(data, dict) and isinstance(data.get("players"), dict):
        return data["players"]
    return data if isinstance(data, dict) else {}


def save_player_data(data: dict[str, Any]) -> None:
    _save_json(PLAYER_DATA_PATH, data)


# ---------------------------------------------------------------------------
# Player helpers
# ---------------------------------------------------------------------------

def _player_key(user_id: int) -> str:
    return str(user_id)


# Имя гнома — один раз за жизнь, до 24 символов; хранится HTML-escaped,
# потому что подставляется во все HTML-рендеры (см. регресс 4503395).
MAX_GNOME_NAME_LEN = 24
GNOME_NAME_CALLBACK_PREFIX = "gnam"
GNOME_PENDING_KEY = "gnome_rename_pending"


def display_username(player: dict[str, Any] | None, username: str) -> str:
    """«Имя (username)» для названного гнома, иначе просто username."""
    player = player or {}
    if player.get("display_named") and player.get("display_name"):
        return _get_text("adventure.named_display", name=player["display_name"], username=username)
    return username


def set_gnome_name(user_id: int, raw: str) -> dict[str, Any]:
    """Назвать гнома (CAS: ровно один раз за жизнь).

    Возвращает {"event": "gnome_named", "name"} |
    {"event": "gnome_already_named", "name"} | {"event": "empty_name"}.
    """
    from html import escape

    with _EXPEDITION_LOCK:
        data = load_player_data()
        player = data.get(_player_key(user_id))
        if not isinstance(player, dict):
            return {"event": "empty_name"}
        _ensure_player_defaults(player)
        if player.get("display_named"):
            return {"event": "gnome_already_named", "name": player.get("display_name") or ""}

        name = (raw or "").strip()[:MAX_GNOME_NAME_LEN]
        if not name:
            return {"event": "empty_name"}
        safe_name = escape(name)
        player["display_name"] = safe_name
        player["display_named"] = True
        save_player_data(data)
    return {"event": "gnome_named", "name": safe_name}


def _admin_rewrite_gnome_name(user_id: int, new_name: str | None) -> dict[str, Any]:
    """Админская перезапись имени гнома: new_name задан — установить
    (CAS в обход), None/" " — сбросить до безымянного.

    Возвращает {"event": "set"/"reset", "name"} | {"event": "empty_name"}.
    """
    from html import escape

    with _EXPEDITION_LOCK:
        data = load_player_data()
        player = data.get(_player_key(user_id))
        if not isinstance(player, dict):
            data[_player_key(user_id)] = {"hp": 200, "max_hp": 200, "lvl": 1}
            player = data[_player_key(user_id)]
        _ensure_player_defaults(player)

        if new_name is None:
            player["display_name"] = ""
            player["display_named"] = False
            save_player_data(data)
            return {"event": "reset", "name": ""}

        name = (new_name or "").strip()[:MAX_GNOME_NAME_LEN]
        if not name:
            return {"event": "empty_name"}
        player["display_name"] = escape(name)
        player["display_named"] = True
        save_player_data(data)
        return {"event": "set", "name": escape(name)}


async def admin_gnome_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Админский контроль имён: /admin_gnome_name <uid> <имя> |
    /admin_gnome_reset <uid>. Игроков хватает за горло их же именами."""
    user = update.effective_user
    if not user or not is_admin(user.id):
        if update.effective_message:
            message = await update.effective_message.reply_text(
                _get_text("adventure.admin_denied")
            )
            await schedule_self_delete(context, message)
        return

    args = context.args or []
    first_token = (update.effective_message.text or "").split()[0] if update.effective_message else ""
    is_reset = "reset" in first_token

    if not args or not args[0].lstrip("-").isdigit():
        usage_key = "adventure.admin_reset_usage" if is_reset else "adventure.admin_name_usage"
        message = await update.effective_message.reply_text(_get_text(usage_key))
        await schedule_self_delete(context, update.effective_message)
        await schedule_self_delete(context, message)
        return

    uid = int(args[0])
    new_name = None if is_reset else " ".join(args[1:])
    result = _admin_rewrite_gnome_name(uid, new_name)

    if result.get("event") == "set":
        text = _get_text("adventure.admin_name_set", uid=uid, name=result["name"])
    elif result.get("event") == "reset":
        text = _get_text("adventure.admin_name_reset", uid=uid)
    else:
        text = _get_text("adventure.admin_name_usage")

    message = await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML)
    await schedule_self_delete(context, update.effective_message)
    await schedule_self_delete(context, message)


async def admin_expedition_finish_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """⚙️ /admin_gnome_finish <uid> — дожать висящую экспедицию вручную.

    Использует тот же_atomic путь, что чекер и таймер: первый вызвавший
    забирает завершение. Возвращает статус: завершилась или осталась
    (активный бой отложит завершение; повтори позже).
    """
    user = update.effective_user
    if not user or not is_admin(user.id):
        if update.effective_message:
            message = await update.effective_message.reply_text(
                _get_text("adventure.admin_denied")
            )
            await schedule_self_delete(context, message)
        return

    args = context.args or []
    if not args or not args[0].lstrip("-").isdigit():
        message = await update.effective_message.reply_text(
            _get_text("adventure.admin_finish_usage")
        )
        await schedule_self_delete(context, update.effective_message)
        await schedule_self_delete(context, message)
        return

    uid = int(args[0])
    logger.info("Admin %s forces expedition finish for %s", user.id, uid)
    await send_expedition_result(uid, None, None, context)

    player = get_player(uid)
    still_active = isinstance(player.get("active_expedition"), dict)
    if still_active:
        text = _get_text("adventure.admin_finish_still", uid=uid)
    else:
        text = _get_text("adventure.admin_finish_done", uid=uid)
    message = await update.effective_message.reply_text(text)
    await schedule_self_delete(context, update.effective_message)
    await schedule_self_delete(context, message)


def get_player(user_id: int, pool: dict | None = None) -> dict[str, Any]:
    data = load_player_data()
    key = _player_key(user_id)
    if key not in data:
        data[key] = {
            "hp": 200,
            "max_hp": 200,
            "lvl": 1,
            "xp": 0,
            "gold": 0,
            "last_fight_time": None,
            "total_fights": 0,
            "wins": 0,
            "inventory": [],
            "active_expedition": None,
        }
        save_player_data(data)
    player = data[key]
    # ensure inventory field exists for older records
    if "inventory" not in player:
        player["inventory"] = []
    # ensure max_hp field exists
    if "max_hp" not in player:
        player["max_hp"] = 200
    # ensure active_expedition field exists
    if "active_expedition" not in player:
        player["active_expedition"] = None
    return player


def update_player(user_id: int, **fields) -> None:
    data = load_player_data()
    key = _player_key(user_id)
    if key not in data:
        get_player(user_id)  # init
        data = load_player_data()
    data[key].update(fields)
    save_player_data(data)


# Serializes claim-style operations on the JSON player store so that a
# completion can never race with a cancellation or a second completion.
_EXPEDITION_LOCK = threading.Lock()

# Окно интерактива поедания в инвентаре: на это время пересаживаем
# очистную джобу сообщения (по умолчанию чистим через минуту).
EAT_SESSION_SECONDS = 300

# Яйцо питомца — ресурс с именем из таблиц локаций (пока dark_forest).
# Единый источник имени и для инвентаря, и для вылупления (handlers/pets.py).
PET_EGG_NAME = "🥚 Яйцо питомца"


def parse_expedition_time(value: Any) -> datetime | None:
    """Parse an ISO datetime written by the expedition flow; UTC-aware."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        logger.warning("Error parsing expedition datetime: %r", value)
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _required_level(loc: dict[str, Any]) -> int:
    """Minimum player level for a location (requirements.min_level)."""
    requirements = loc.get("requirements") or {}
    try:
        return int(requirements.get("min_level", loc.get("level", 1)) or 1)
    except (TypeError, ValueError):
        return 1


def _xp_for_next_level(level: int) -> int:
    """XP до левелапа: ровно 100 за любой уровень."""
    return 100


def _apply_level_ups(player: dict[str, Any]) -> bool:
    """Spend XP on levels: each level adds +20 max HP and heals +20.

    Mutates the player dict in place; returns True when at least one level
    was gained.
    """
    leveled_up = False
    while player.get("xp", 0) >= _xp_for_next_level(player.get("lvl", 1)):
        player["xp"] = player.get("xp", 0) - _xp_for_next_level(player.get("lvl", 1))
        player["lvl"] = player.get("lvl", 1) + 1
        player["max_hp"] = 200 + (player["lvl"] - 1) * 20
        player["hp"] = min(player["max_hp"], player.get("hp", 200) + 20)
        leveled_up = True
    return leveled_up


def _ru_plural(n: int, one: str, few: str, many: str) -> str:
    """Русская форма множественного числа: 1 раунд / 2 раунда / 5 раундов."""
    n = abs(int(n)) % 100
    if 11 <= n <= 14:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def _render_battle_log(battle_log: list[dict[str, Any]]) -> list[str]:
    """Lines describing the expedition's battles for the final report."""
    lines = []
    for entry in battle_log:
        if entry.get("result") != "victory":
            continue
        enemy = entry.get("enemy") or _get_text("adventure.unknown_mob")
        rounds = int(entry.get("rounds", 0) or 0)
        gender = _get_mob_gender(enemy)
        key = {2: "battle.report_defeated_f", 3: "battle.report_defeated_n"}.get(
            gender, "battle.report_defeated"
        )
        line = _get_text(
            key,
            mob_name=enemy,
            rounds=rounds,
            rounds_word=_ru_plural(rounds, "раунд", "раунда", "раундов"),
        )
        extras = []
        if entry.get("xp"):
            extras.append(_get_text("battle.report_xp", xp=entry["xp"]))
        if entry.get("item"):
            extras.append(_get_text("battle.report_item", item=entry["item"]))
        if extras:
            line += " (" + ", ".join(extras) + ")"
        lines.append(line)
    if not lines:
        lines.append(_get_text("battle.quiet_path"))
    return lines


def _expedition_job_name(user_id: int) -> str:
    """JobQueue name for the user's scheduled expedition completion."""
    return f"expedition_{user_id}"


def claim_active_expedition(user_id: int) -> dict[str, Any] | None:
    """Atomically detach the player's active expedition (first caller wins).

    Returns the expedition record, or None if there was nothing to claim
    (already finished or cancelled). Used by the completion flow and by the
    early-return callback so the expedition can only be resolved once.
    """
    with _EXPEDITION_LOCK:
        data = load_player_data()
        player = data.get(_player_key(user_id))
        if not isinstance(player, dict):
            return None
        expedition = player.get("active_expedition")
        if not isinstance(expedition, dict):
            return None
        player["active_expedition"] = None
        save_player_data(data)
        return expedition


def _format_resource(resource: dict[str, Any]) -> str:
    """Format a resource for display."""
    name = resource.get("name", resource.get("item_id", "Unknown"))
    qty = resource.get("quantity", 1)
    return f"• {name} x{qty}"


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _get_text(key: str, **kwargs) -> str:
    try:
        return get_text(key, **kwargs)
    except Exception:
        # fallback if key missing in adventure.yaml
        return key


def _get_mob_gender(mob_name: str) -> int:
    """Определяет род моба по его имени для правильного склонения глаголов.
    
    Returns: 1 = мужской, 2 = женский, 3 = средний
    """
    # Сначала проверяем известные мобы с явными названиями
    # Если моб имеет название с определённым родом
    
    # ЖЕНЫСКИЙ РОД (окончания -а, -я)
    if any(mob_name.lower().endswith(suf) for suf in ['а', 'я']):
        # Исключения: имена существительные мужского рода на -а, -я
        male_exceptions = get_text_list("common.mob_gender.male_exceptions")
        if mob_name.lower() not in male_exceptions:
            return 2
    
    # СРЕДНИЙ РОД (окончания -о, -е, -мя, -и, -ь после шипящих)
    if any(mob_name.lower().endswith(suf) for suf in ['о', 'е', 'мя', 'и', 'у']):
        return 3
    if mob_name.lower().endswith('ь') and mob_name.lower()[-2] in ['ш', 'ч', 'щ', 'ж']:
        return 3
    
    # МУЖСКОЙ РОД (остальные случаи, окончания согласная, -ь)
    return 1


def _get_mob_gendered_text(mob_name: str, text_key_base: str) -> str:
    """Получает текст с правильным родом для моба.
    
    Args:
        mob_name: Имя моба
        text_key_base: Базовый ключ текста (например 'mob_survived')
    
    Returns:
        Текст с правильным родом
    """
    gender = _get_mob_gender(mob_name)
    if gender == 2:
        return _get_text(f"adventure.{text_key_base}_f", mob_name=mob_name)
    elif gender == 3:
        return _get_text(f"adventure.{text_key_base}_n", mob_name=mob_name)
    else:
        return _get_text(f"adventure.{text_key_base}", mob_name=mob_name)


async def _expedition_complete_callback(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback function for job_queue when expedition completes.
    
    Job data must contain user_id; loc_id / username / chat_id are
    optional and fall back to the stored expedition record.
    """
    job_data = context.job.data if context.job else None
    if not job_data:
        logger.error("No job data found for expedition completion")
        return
    
    user_id = job_data.get("user_id")
    loc_id = job_data.get("loc_id")
    username = job_data.get("username")
    chat_id = job_data.get("chat_id")  # chat_id where expedition was started
    
    if not user_id:
        logger.error(f"Invalid job data: {job_data}")
        return

    # Send expedition result
    await send_expedition_result(
        user_id,
        job_data.get("loc_id"),
        job_data.get("username"),
        context,
        job_data.get("chat_id"),
    )


# ---------------------------------------------------------------------------
# Keyboard builders
# ---------------------------------------------------------------------------

def _locations_keyboard(locations: dict[str, Any]) -> InlineKeyboardMarkup:
    buttons = []
    for loc_id, loc in locations.items():
        btn_text = loc.get("name", loc_id)
        buttons.append(
            [InlineKeyboardButton(btn_text, callback_data=f"adv_loc_{loc_id}")]
        )
    # Кузня живёт рядом с локациями — с ней удобно ходить заматерелым.
    buttons.append([InlineKeyboardButton(
        _get_text("adventure.forge_button"), callback_data="adv_forge",
    )])
    return InlineKeyboardMarkup(buttons)


def _back_to_locations_keyboard() -> InlineKeyboardMarkup:
    """Кнопка «назад к списку локаций» для тупиковых экранов (low_level и пр.)."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(
            _get_text("adventure.button_back_to_locations"), callback_data="adv_back",
        )
    ]])


# ---------------------------------------------------------------------------
# Кузня: крафт снаряжения и надевание/снятие
# ---------------------------------------------------------------------------

FORGE_CRAFT_CALLBACK = "adv_craft_"
FORGE_ON_CALLBACK = "adv_gear_on_"
FORGE_OFF_CALLBACK = "adv_gear_off_"


def _load_gear_catalog() -> dict[str, Any]:
    return (load_global_pool().get("gear") or {})


def _entry_qty(entry: dict[str, Any]) -> int:
    """Количество единиц в записи инвентаря (None → 1; нули честны)."""
    qty = entry.get("quantity")
    return 1 if qty is None else int(qty)


def _inventory_counts(player: dict[str, Any]) -> dict[str, int]:
    """Сколько единиц каждого имени лежит в инвентаре (ресурсы + снаряжение)."""
    counts: dict[str, int] = {}
    for entry in player.get("inventory") or []:
        name = entry.get("name")
        if name:
            counts[name] = counts.get(name, 0) + _entry_qty(entry)
    return counts


def _check_materials(player: dict[str, Any], materials: list[dict[str, Any]]) -> list[tuple[str, int, int]]:
    """Недостающие материалы: [(имя, нужно, есть)]. Пусто — всё хватает."""
    counts = _inventory_counts(player)
    missing = []
    for mat in materials or []:
        need = int(mat.get("qty", 1) or 1)
        have = counts.get(mat.get("name", ""), 0)
        if have < need:
            missing.append((mat.get("name", "?"), need, have))
    return missing


def _consume_materials(player: dict[str, Any], materials: list[dict[str, Any]]) -> None:
    """Списать материалы из инвентаря (после успешной проверки)."""
    remaining = list(player.get("inventory") or [])
    for mat in materials or []:
        qty_left = int(mat.get("qty", 1) or 1)
        name = mat.get("name", "")
        for entry in remaining:
            if qty_left <= 0:
                break
            if entry.get("name") != name:
                continue
            take = min(qty_left, _entry_qty(entry))
            entry["quantity"] = _entry_qty(entry) - take
            qty_left -= take
        remaining = [e for e in remaining if _entry_qty(e) > 0]
    player["inventory"] = remaining


def _stock_lines(player: dict[str, Any], recipes: list[dict[str, Any]]) -> list[str]:
    """Кузне важно видеть, ЧО у гнома есть: запасы материалов для рецептов."""
    counts = _inventory_counts(player)
    needed: dict[str, None] = {}
    for recipe in recipes:
        for mat in recipe.get("materials") or []:
            needed[mat.get("name", "?")] = None
    needed_names = sorted(needed)

    lines = []
    for name in needed_names:
        have = counts.get(name, 0)
        lines.append(_get_text(
            "adventure.forge_stock_line", name=name, count=have,
        ))
    if not lines:
        lines.append(_get_text("adventure.forge_stock_empty"))
    return lines


def _equipment_with_names(player: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """equipment {slot: gearId} в развёрнутом виде {slot: {…, name}}."""
    catalog = _load_gear_catalog()
    equipped: dict[str, dict[str, Any]] = {}
    for slot, gear_id in (player.get("equipment") or {}).items():
        entry = catalog.get(gear_id)
        if isinstance(entry, dict):
            equipped[slot] = {**entry, "_id": gear_id}
    return equipped


def _add_gear_to_inventory(player: dict[str, Any], gear_id: str) -> str:
    """Положить предмет снаряжения в инвентарь (экземпляр с gear_id)."""
    catalog = _load_gear_catalog()
    entry = catalog.get(gear_id) or {}
    player["inventory"] = list(player.get("inventory") or []) + [
        {"name": entry.get("name", gear_id), "gear_id": gear_id, "quantity": 1},
    ]
    return entry.get("name", gear_id)


def _forge_text(player: dict[str, Any]) -> str:
    """Экран кузни: экипировка + рецепты с их материалами."""
    equipped = _equipment_with_names(player)
    weapon_name = (equipped.get("weapon") or {}).get("name")
    armor_names = [equipped[slot]["name"] for slot in ("head", "body", "dick") if slot in equipped]
    trinket_name = (equipped.get("trinket") or {}).get("name")

    glossary = _load_gear_catalog()
    counts = _inventory_counts(player)
    recipe_lines = []
    for recipe in load_recipes():
        entry = glossary.get(recipe.get("result")) or {}
        costs = " · ".join(
            f"{m.get('name', '?')} ×{m.get('qty', 1)}/{counts.get(m.get('name', ''), 0)}"
            for m in recipe.get("materials") or []
        )
        recipe_lines.append(_get_text(
            "adventure.forge_recipe_line", name=entry.get("name", recipe.get("id", "?")), costs=costs,
        ))

    return "\n".join([
        _get_text("adventure.forge_title"),
        "",
        _get_text("adventure.forge_weapon_line", name=weapon_name or _get_text("adventure.forge_nothing")),
        _get_text(
            "adventure.forge_armor_line",
            names=", ".join(armor_names) or _get_text("adventure.forge_nothing"),
        ),
        _get_text("adventure.forge_trinket_line", name=trinket_name or _get_text("adventure.forge_nothing")),
        "",
        _get_text("adventure.forge_craft_section"),
        *recipe_lines,
        "",
        _get_text("adventure.forge_stock_header"),
        *_stock_lines(player, load_recipes()),
        "",
        _get_text("adventure.forge_hint"),
    ])


def _forge_keyboard(player: dict[str, Any]) -> InlineKeyboardMarkup:
    """Кнопки кузни: крафт (по одному рецепту на кнопку), снять/надеть, назад."""
    rows: list[list[InlineKeyboardButton]] = []
    for recipe in load_recipes():
        entry = (_load_gear_catalog().get(recipe.get("result")) or {})
        rows.append([InlineKeyboardButton(
            entry.get("name", recipe.get("id", "?")),
            callback_data=f"{FORGE_CRAFT_CALLBACK}{recipe.get('id')}",
        )])

    equipped = _equipment_with_names(player)
    for slot in ("weapon", "head", "body", "dick", "trinket"):
        piece = equipped.get(slot)
        if piece:
            rows.append([InlineKeyboardButton(
                _get_text("adventure.forge_unequip_button", name=piece.get("name", "?")),
                callback_data=f"{FORGE_OFF_CALLBACK}{slot}",
            )])

    counts: dict[str, int] = {}
    for entry in player.get("inventory") or []:
        gid = entry.get("gear_id")
        if gid:
            counts[gid] = counts.get(gid, 0) + 1
    catalog = _load_gear_catalog()
    for gear_id, count in counts.items():
        entry = catalog.get(gear_id) or {}
        rows.append([InlineKeyboardButton(
            _get_text(
                "adventure.forge_equip_button",
                name=entry.get("name", gear_id), count=count,
            ),
            callback_data=f"{FORGE_ON_CALLBACK}{gear_id}",
        )])

    rows.append([InlineKeyboardButton(
        _get_text("adventure.button_back_to_locations"), callback_data="adv_back",
    )])
    return InlineKeyboardMarkup(rows)


async def _render_forge(query, user_id: int) -> None:
    """Перерисовать кузню после крафта/надевания/снятия."""
    player = get_player(user_id)
    await query.edit_message_text(
        _forge_text(player),
        reply_markup=_forge_keyboard(player),
        parse_mode=ParseMode.HTML,
    )


def _location_details_keyboard(loc_id: str, has_active_expedition: bool = False) -> InlineKeyboardMarkup:
    """Build keyboard for location details with expedition button."""
    if has_active_expedition:
        action_text = _get_text("adventure.button_cancel_expedition")
        action_callback = f"adv_cancel_expedition_{loc_id}"
    else:
        action_text = _get_text("adventure.button_start_expedition")
        action_callback = f"adv_expedition_{loc_id}"

    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(action_text, callback_data=action_callback)],
            [InlineKeyboardButton(
                _get_text("adventure.button_back_to_locations"), callback_data="adv_back"
            )],
        ]
    )


_EMOJI_RE = re.compile(r"^(\S\uFE0F?)")


def _leading_emoji(display_name: str) -> str:
    """Первый знак эмодзи из display_name ('🌲Охуенно…' -> '🌲').

    Срез [:2] захватывал эмодзи плюс первую букву названия и удваивал её
    («ООхуенно», «ЗЗаброшенная»); вариантный селектор «⛏️» сохраняем.
    """
    match = _EMOJI_RE.match(display_name or "")
    return match.group(1) if match else "🌲"


def _format_rewards_text(loc: dict[str, Any]) -> str:
    """Human-readable list of the location's resources with drop chances."""
    rewards_text = _get_text("adventure.resources_loot", resources="—")
    rewards_items = []
    for item in loc.get("resources", []) or []:
        item_name = item.get("name", item.get("id", "Resource"))
        chance = int(item.get("chance", 0) * 100)
        rewards_items.append(f"{item_name} ({chance}%)")
    if rewards_items:
        rewards_text = "• " + "\n• ".join(rewards_items)
    return rewards_text


def _build_location_details_text(user, loc: dict[str, Any], loc_id: str) -> str:
    """Render the location card shown when a location is selected."""
    boss_name = _get_text("adventure.no_boss_data")
    boss_chance = "0"
    bosses = loc.get("bosses", []) or []
    if bosses:
        first_boss = bosses[0]
        boss_name = first_boss.get("display_name", first_boss.get("name", boss_name))
        boss_chance = str(int(first_boss.get("encounter_chance", 0) * 100))

    username = user.username or user.first_name
    player = get_player(user.id)
    return _get_text(
        "adventure.location_detailed_info",
        username=display_username(player, username),
        location_emoji=_leading_emoji(loc.get("display_name") or ""),
        location_name=loc.get("name", loc_id),
        location_description=loc.get("full_description", ""),
        min_level=_required_level(loc),
        duration=loc.get("duration_minutes", 5),
        rewards=_format_rewards_text(loc),
        boss_name=boss_name,
        boss_chance=boss_chance,
    )


# ---------------------------------------------------------------------------
# Main handlers
# ---------------------------------------------------------------------------

async def adventure_name_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Призыв /adventure_name: заставить гнома стать Кем-То (один раз навсегда)."""
    user = update.effective_user
    if not user:
        return
    player = get_player(user.id)
    username = user.username or user.first_name or _get_text("common.user.default_title")

    if player.get("display_named"):
        message = await update.effective_message.reply_text(
            _get_text("adventure.name_already", name=player.get("display_name", "")),
            parse_mode=ParseMode.HTML,
        )
    else:
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton(
                _get_text("adventure.name_accept_button"),
                callback_data=f"{GNOME_NAME_CALLBACK_PREFIX}_{user.id}_confirm",
            )
        ]])
        message = await update.effective_message.reply_text(
            _get_text("adventure.name_intro", username=username),
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
    await schedule_self_delete(context, update.effective_message)
    await schedule_self_delete(context, message)


async def gnome_name_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопка «Я ХОРОШО ПОДУМАЛ»: тумблер ожидания имени текстом."""
    query = update.callback_query
    if query is None:
        return
    user = update.effective_user
    parts = (query.data or "").split("_")  # gnam_{owner}_confirm
    if user is None or len(parts) != 3 or parts[0] != GNOME_NAME_CALLBACK_PREFIX:
        await query.answer()
        return
    try:
        owner_id = int(parts[1])
    except ValueError:
        await query.answer()
        return
    if user.id != owner_id:
        await query.answer(_get_text("adventure.not_your_button"))
        return

    player = get_player(user.id)
    if player.get("display_named"):
        await query.answer(_get_text(
            "adventure.name_already", name=player.get("display_name", ""),
        ))
        return

    if context.user_data.get(GNOME_PENDING_KEY):
        context.user_data.pop(GNOME_PENDING_KEY, None)
        await query.answer(_get_text("adventure.name_cancel_toast"))
        return

    context.user_data[GNOME_PENDING_KEY] = query.message.chat_id if query.message else user.id
    await query.answer(_get_text("adventure.name_pending_toast"))


async def adventure_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Entry point: /adventure — show available locations."""
    locations = load_locations()
    text = _get_text("adventure.welcome")
    # Уборка: команда игрока и меню локаций живут две минуты.
    await schedule_self_delete(context, update.effective_message, ADVENTURE_DELETE_DELAY)
    message = await update.effective_message.reply_text(
        text, reply_markup=_locations_keyboard(locations), parse_mode='HTML'
    )
    await schedule_self_delete(context, message, ADVENTURE_DELETE_DELAY)


async def adventure_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle all adventure inline callbacks."""
    query = update.callback_query

    data = query.data or ""
    user = update.effective_user
    if not user:
        await query.answer()
        return

    # Тостовые ветки кузни отвечают на клик текстом сами — общий пустой
    # ответ НЕДОПУСТИМ до них (второй answer уже не показывает текст).
    toast_prefixes = (FORGE_CRAFT_CALLBACK, FORGE_ON_CALLBACK, FORGE_OFF_CALLBACK)
    if not data.startswith(toast_prefixes):
        await query.answer()

    # ------------------------------------------------------------------
    # Back to locations list
    # ------------------------------------------------------------------
    if data == "adv_back":
        locations = load_locations()
        await query.edit_message_text(
            _get_text("adventure.welcome"),
            reply_markup=_locations_keyboard(locations),
            parse_mode='HTML',
        )
        return

    # ------------------------------------------------------------------
    # Кузня
    # ------------------------------------------------------------------
    if data == "adv_forge":
        player = get_player(user.id)
        await query.edit_message_text(
            _forge_text(player),
            reply_markup=_forge_keyboard(player),
            parse_mode=ParseMode.HTML,
        )
        return

    if data.startswith(FORGE_CRAFT_CALLBACK):
        recipe_id = data[len(FORGE_CRAFT_CALLBACK):]
        recipe = next((r for r in load_recipes() if r.get("id") == recipe_id), None)
        if not recipe:
            await query.answer(_get_text("adventure.forge_craft_unknown"))
            return
        player = get_player(user.id)
        missing = _check_materials(player, recipe.get("materials") or [])
        if missing:
            fragment = ", ".join(f"{name} ×{need} (есть {have})" for name, need, have in missing[:3])
            await query.answer(_get_text("adventure.forge_not_enough", missing=fragment)[:200])
            return
        _consume_materials(player, recipe.get("materials") or [])
        crafted_name = _add_gear_to_inventory(player, recipe.get("result") or "")
        update_player(user.id, inventory=player["inventory"])
        await query.answer(_get_text("adventure.forge_crafted", name=crafted_name)[:200])
        await _render_forge(query, user.id)
        return

    if data.startswith(FORGE_ON_CALLBACK):
        gear_id = data[len(FORGE_ON_CALLBACK):]
        catalog = _load_gear_catalog()
        entry = catalog.get(gear_id)
        if not entry:
            await query.answer(_get_text("adventure.forge_craft_unknown"))
            return
        slot = entry.get("slot")
        if not slot:
            await query.answer()
            return
        player = get_player(user.id)
        index = next((i for i, e in enumerate(player.get("inventory") or [])
                      if e.get("gear_id") == gear_id), None)
        if index is None:
            await query.answer(_get_text("adventure.forge_missing_in_inventory")[:200])
            return
        inventory = list(player.get("inventory") or [])
        del inventory[index]
        equipment = dict(player.get("equipment") or {})
        previous = equipment.get(slot)
        equipment[slot] = gear_id
        update_player(user.id, inventory=inventory, equipment=equipment)
        await query.answer(_get_text("adventure.forge_equip_toast", name=entry.get("name", gear_id))[:200])
        await _render_forge(query, user.id)
        return

    if data.startswith(FORGE_OFF_CALLBACK):
        slot = data[len(FORGE_OFF_CALLBACK):]
        player = get_player(user.id)
        equipment = dict(player.get("equipment") or {})
        gear_id = equipment.get(slot)
        if not gear_id:
            await query.answer()
            return
        entry = _load_gear_catalog().get(gear_id) or {}
        _add_gear_to_inventory(player, gear_id)
        equipment[slot] = None
        update_player(user.id, inventory=player["inventory"], equipment=equipment)
        await query.answer(_get_text("adventure.forge_unequip_toast", name=entry.get("name", gear_id))[:200])
        await _render_forge(query, user.id)
        return

    # ------------------------------------------------------------------
    # Cancel / close
    # ------------------------------------------------------------------
    if data == "adv_cancel":
        await query.edit_message_text(_get_text("adventure.cancelled"), parse_mode='HTML')
        return

    # ------------------------------------------------------------------
    # Location selected — show details
    # ------------------------------------------------------------------
    if data.startswith("adv_loc_"):
        loc_id = data[len("adv_loc_") :]
        locations = load_locations()
        loc = locations.get(loc_id)
        if not loc:
            await query.edit_message_text(_get_text("adventure.unknown_location"), parse_mode='HTML')
            return

        player = get_player(user.id)

        # Если время экспедиции уже вышло — завершаем её сразу,
        # чтобы игрок не терял награды до срабатывания таймера.
        active_exp = player.get("active_expedition")
        if isinstance(active_exp, dict):
            end_dt = parse_expedition_time(active_exp.get("end_time"))
            if end_dt is not None and datetime.now(timezone.utc) >= end_dt:
                await send_expedition_result(user.id, None, None, context)
                player = get_player(user.id)
                active_exp = player.get("active_expedition")

        has_active_expedition = active_exp is not None

        # Level check
        required_level = _required_level(loc)
        if player.get("lvl", 1) < required_level:
            await query.edit_message_text(
                _get_text("adventure.low_level", level=required_level),
                parse_mode='HTML',
                reply_markup=_back_to_locations_keyboard(),
            )
            return

        # Информация об активной экспедиции, если есть
        expedition_info = None
        if has_active_expedition and isinstance(active_exp, dict):
            start_dt = parse_expedition_time(active_exp.get("start_time"))
            end_dt = parse_expedition_time(active_exp.get("end_time"))
            exp_loc_name = active_exp.get("location_name", _get_text("adventure.unknown_location_name"))
            if start_dt is not None and end_dt is not None:
                now = datetime.now(timezone.utc)
                total_duration = (end_dt - start_dt).total_seconds()
                elapsed = max(0.0, (now - start_dt).total_seconds())
                remaining = max(0.0, total_duration - elapsed)
                expedition_info = {
                    "location": exp_loc_name,
                    "remaining_minutes": int(remaining // 60),
                    "remaining_seconds": int(remaining) % 60,
                }

        loc_text = _build_location_details_text(user, loc, loc_id)

        # Добавляем информацию об активной экспедиции если есть
        if has_active_expedition and expedition_info:
            loc_text += "\n\n" + _get_text(
                "adventure.expedition_in_progress",
                location=expedition_info["location"],
                minutes=expedition_info["remaining_minutes"],
                seconds=expedition_info["remaining_seconds"],
            )

        # Гном дерётся прямо сейчас
        if player.get("active_battle"):
            loc_text += "\n\n" + _get_text("battle.in_progress")

        await query.edit_message_text(
            loc_text,
            reply_markup=_location_details_keyboard(loc_id, has_active_expedition=has_active_expedition),
            parse_mode='HTML',
        )
        return

    # ------------------------------------------------------------------
    # Cancel expedition early
    # ------------------------------------------------------------------
    if data.startswith("adv_cancel_expedition_"):
        loc_id = data[len("adv_cancel_expedition_") :]
        logger.info(f"Cancel expedition callback: loc_id={loc_id}, user_id={user.id}")

        locations = load_locations()
        loc = locations.get(loc_id)
        if not loc:
            logger.error(f"Unknown location: {loc_id}")
            await query.edit_message_text(_get_text("adventure.unknown_location"), parse_mode='HTML')
            return

        # Атомарно забираем экспедицию, чтобы таймер завершения
        # не посчитал её повторно.
        expedition = claim_active_expedition(user.id)
        if expedition is None:
            logger.warning(f"User {user.id} tried to cancel but has no active expedition")
            await query.edit_message_text(
                _get_text("adventure.no_active_expedition"),
                parse_mode='HTML'
            )
            return

        # Снимаем запланированные джобы (завершение, энкаунтер, фаза боя)
        # и прерываем бой, если он идёт.
        from handlers.battle import abort_battle, battle_job_name, encounter_job_name

        if context.job_queue:
            for job_name in (
                _expedition_job_name(user.id),
                encounter_job_name(user.id),
                battle_job_name(user.id),
            ):
                for job in context.job_queue.get_jobs_by_name(job_name):
                    job.schedule_removal()
        abort_battle(user.id)

        # Calculate elapsed time and refund
        start_dt = parse_expedition_time(expedition.get("start_time"))
        end_dt = parse_expedition_time(expedition.get("end_time"))
        remaining_minutes = 1
        if start_dt is not None and end_dt is not None:
            now = datetime.now(timezone.utc)
            total_duration = (end_dt - start_dt).total_seconds()
            elapsed = max(0.0, (now - start_dt).total_seconds())
            remaining = max(0.0, total_duration - elapsed)
            remaining_minutes = max(1, int(remaining / 60))

        logger.info(f"Expedition cancelled for user {user.id}. Remaining time: {remaining_minutes} min")

        # Show location menu again with expedition cleared
        loc_text = _build_location_details_text(user, loc, loc_id)
        loc_text += "\n\n" + _get_text(
            "adventure.expedition_returned_early", minutes=remaining_minutes
        )

        await query.edit_message_text(
            loc_text,
            reply_markup=_location_details_keyboard(loc_id, has_active_expedition=False),
            parse_mode='HTML'
        )
        return

    # ------------------------------------------------------------------
    # Start expedition
    # ------------------------------------------------------------------
    if data.startswith("adv_expedition_"):
        loc_id = data[len("adv_expedition_") :]
        logger.info(f"Start expedition callback: loc_id={loc_id}, user_id={user.id}")
        
        locations = load_locations()
        loc = locations.get(loc_id)
        if not loc:
            logger.error(f"Unknown location: {loc_id}")
            return

        player = get_player(user.id)
        logger.info(f"Player data before expedition: {player}")
        
        # Check if already on expedition
        if player.get("active_expedition"):
            logger.warning(f"User {user.id} tried to start expedition but already has active_expedition: {player.get('active_expedition')}")
            # Show detailed info about current expedition
            current_exp = player.get("active_expedition", {})
            exp_loc_name = current_exp.get("location_name", _get_text("adventure.unknown_location_name"))
            await query.edit_message_text(
                _get_text("adventure.expedition_already_active", location=exp_loc_name),
                parse_mode='HTML'
            )
            return

        # Check level
        required_level = _required_level(loc)
        if player.get("lvl", 1) < required_level:
            await query.edit_message_text(
                _get_text("adventure.low_level", level=required_level),
                parse_mode='HTML',
                reply_markup=_back_to_locations_keyboard(),
            )
            return

        duration_seconds = int(
            loc.get("expedition_duration_seconds")
            or loc.get("duration")
            or (int(loc.get("duration_minutes", 5)) * 60)
        )
        duration_minutes = max(1, duration_seconds // 60)

        # Get the chat_id where the expedition was started
        chat_id = query.message.chat_id if query.message else user.id

        # Create expedition record
        expedition_start = datetime.now(timezone.utc)
        expedition_end = expedition_start + timedelta(seconds=duration_seconds)

        # Энкаунтер: случайный момент внутри экспедиции (см. handlers/battle.py).
        # Локальный импорт: battle импортирует этот модуль — разрываем цикл.
        from handlers.battle import encounter_job_name, plan_encounter, trigger_encounter_job

        encounter_delay = plan_encounter(loc, duration_seconds)
        expedition_data = {
            "location_id": loc_id,
            "location_name": loc.get("name", loc_id),
            "start_time": expedition_start.isoformat(),
            "end_time": expedition_end.isoformat(),
            "duration_seconds": duration_seconds,
            "chat_id": chat_id,
            "username": user.username or user.first_name,
            "encounter_scheduled": encounter_delay is not None,
        }
        if encounter_delay is not None:
            expedition_data["encounter_at"] = (
                expedition_start + timedelta(seconds=encounter_delay)
            ).isoformat()

        update_player(user.id, active_expedition=expedition_data)
        
        # Delete the location message and send expedition start message to the same chat
        await query.delete_message()
        
        expedition_text = _get_text(
            "adventure.expedition_start",
            username=display_username(player, user.username or user.first_name),
            location_name=loc.get("name", loc_id),
            duration=duration_minutes
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=expedition_text,
            parse_mode='HTML'
        )
        
        # Schedule completion message - save chat_id to send result to the same chat
        context.job_queue.run_once(
            _expedition_complete_callback,
            duration_seconds,
            name=_expedition_job_name(user.id),
            data={"user_id": user.id, "loc_id": loc_id, "username": user.username or user.first_name, "chat_id": chat_id}
        )

        # Случайный энкаунтер внутри таймера экспедиции.
        if encounter_delay is not None:
            context.job_queue.run_once(
                trigger_encounter_job,
                encounter_delay,
                name=encounter_job_name(user.id),
                data={"user_id": user.id},
            )

        return

    # ------------------------------------------------------------------
    # Legacy boss-related callbacks (deprecated but kept for compatibility)
    # ------------------------------------------------------------------
    if data.startswith("adv_boss_") or data.startswith("adv_fight_"):
        await query.edit_message_text(
            _get_text("adventure.feature_deprecated"),
            parse_mode='HTML'
        )
        return


def _ensure_player_defaults(player: dict[str, Any]) -> None:
    """Ensure legacy records carry every field the expedition flow needs."""
    player.setdefault("hp", 200)
    player.setdefault("max_hp", 200)
    player.setdefault("lvl", 1)
    player.setdefault("xp", 0)
    player.setdefault("gold", 0)
    player.setdefault("inventory", [])
    player.setdefault("total_expeditions", 0)
    player.setdefault("total_fights", 0)
    player.setdefault("wins", 0)
    player.setdefault("active_expedition", None)
    player.setdefault("active_battle", None)
    player.setdefault("equipment", {})
    player.setdefault("display_named", False)
    player.setdefault("display_name", "")
    player.setdefault("pet", None)


def _roll_resources(loc: dict[str, Any]) -> list[dict[str, Any]]:
    """Roll the location's resource drop table.

    quantity_min/quantity_max в записи ресурса — сколько единиц падает
    за один бросок (каждая — отдельная запись инвентаря).
    """
    resources = []
    for resource in loc.get("resources", []) or []:
        if random.random() < resource.get("chance", 0):
            name = resource.get("name", resource.get("id", "Unknown"))
            qty_min = int(resource.get("quantity_min", 1) or 1)
            qty_max = int(resource.get("quantity_max", qty_min) or qty_min)
            for _ in range(max(1, random.randint(qty_min, qty_max))):
                resources.append({"name": name, "quantity": 1})
    return resources


def finish_expedition(
    user_id: int,
    loc_id: str | None = None,
    username: str | None = None,
) -> dict[str, Any] | None:
    """Atomically claim the player's active expedition and settle it.

    Interactive battles are resolved while the expedition runs (see
    handlers/battle.py); here we only roll the location resources, apply
    them, and render the final report from the expedition's battle log.

    The expedition is cleared and all rewards are applied in a single write,
    so whoever fires first (the timer job, the expiration checker or the
    player's own click) is the only one to settle the expedition.

    Returns a dict with the result text and delivery info, or None when
    there was no active expedition to finish.
    """
    with _EXPEDITION_LOCK:
        data = load_player_data()
        player = data.get(_player_key(user_id))
        if not isinstance(player, dict):
            return None
        _ensure_player_defaults(player)

        expedition = player.get("active_expedition")
        if not isinstance(expedition, dict):
            return None
        player["active_expedition"] = None

        loc_id = loc_id or expedition.get("location_id")
        username = username or expedition.get("username") or _get_text("common.user.default_title")
        username = display_username(player, username)
        chat_id = expedition.get("chat_id")
        battle_log = expedition.get("battle_log") or []

        locations = load_locations()
        loc = locations.get(loc_id, {})

        # Resources from the location's drop table (battles already paid
        # out their XP, loot and damage while the expedition ran).
        resources = _roll_resources(loc)
        if resources:
            player["inventory"] = player.get("inventory", []) + resources
        player["total_expeditions"] = player.get("total_expeditions", 0) + 1
        leveled_up = _apply_level_ups(player)

        save_player_data(data)

    # Build the report (pure formatting, no store access).
    result_lines = []
    if resources:
        res_list = "\n".join(_format_resource(r) for r in resources)
        result_lines.append(_get_text("adventure.expedition_resources", resources=res_list))
    else:
        result_lines.append(_get_text("adventure.expedition_no_resources"))

    battle_lines = _render_battle_log(battle_log)
    won_any = any(entry.get("result") == "victory" for entry in battle_log)
    total_xp = sum(int(entry.get("xp", 0) or 0) for entry in battle_log)

    if resources or won_any:
        result_status = _get_text("adventure.expedition_result_success")
    else:
        result_status = _get_text("adventure.expedition_result_failure")

    summary_text = _get_text(
        "adventure.expedition_final_summary",
        username=username,
        hp=player["hp"],
        max_hp=player["max_hp"],
        total_xp=total_xp,
        resources_summary="\n".join(result_lines),
        encounters_summary="\n".join(battle_lines),
    )

    text = result_status + "\n\n" + summary_text
    if leveled_up:
        text += "\n\n" + _get_text(
            "adventure.level_up", level=player["lvl"], max_hp=player["max_hp"]
        )

    return {
        "user_id": user_id,
        "loc_id": loc_id,
        "username": username,
        "chat_id": chat_id,
        "text": text,
    }


async def adventure_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /adventure_stats command - show player stats."""
    user = update.effective_user
    if not user:
        return
    
    player = get_player(user.id)
    
    # Get stats
    level = player.get("lvl", 1)
    current_hp = player.get("hp", 200)
    max_hp = player.get("max_hp", 200)
    gold = player.get("gold", 0)
    xp = player.get("xp", 0)
    total_expeditions = player.get("total_expeditions", 0)
    inventory = player.get("inventory", [])
    
    # Calculate expedition success/fail counts (using total_fights for now as proxy)
    total_fights = player.get("total_fights", 0)
    wins = player.get("wins", 0)
    failed = total_fights - wins
    
    # Get username for display
    username = user.username or user.first_name or _get_text("common.user.default_title")
    
    # Build stats message
    stats_lines = [
        _get_text("adventure.adventure_stats_title"),
        "👤 <b>"
        + display_username(player, username)
        + "</b>",
        "",
        _get_text("adventure.adventure_stats_level", level=level),
        _get_text("adventure.adventure_stats_hp", current=current_hp, max=max_hp),
        _get_text("adventure.adventure_stats_gold", gold=gold),
        _get_text("adventure.adventure_stats_xp", xp=xp),
        _get_text(
            "adventure.adventure_stats_xp_left",
            left=max(0, _xp_for_next_level(level) - xp),
            level=level + 1,
        ),
        _get_text("adventure.adventure_stats_expeditions", count=total_expeditions),
        _get_text("adventure.adventure_stats_expeditions_success", success=wins),
    ]
    
    pet = player.get("pet")
    if isinstance(pet, dict) and pet.get("name"):
        stats_lines.append(_get_text(
            "pets.stats_line",
            pet=f"{pet['name']} (ур. {int(pet.get('level', 1))})",
        ))

    if failed > 0:
        stats_lines.append(_get_text("adventure.adventure_stats_expeditions_failed", failed=failed))
    
    # Уборка: сама команда игрока и ответ бота живут минуту.
    await schedule_self_delete(context, update.effective_message)
    message = await update.effective_message.reply_text(
        "\n".join(stats_lines),
        parse_mode=ParseMode.HTML
    )
    await schedule_self_delete(context, message)


def _item_hash(item_name: str) -> str:
    """Короткий стабильный хеш имени предмета (для callback_data)."""
    return hashlib.sha1(item_name.encode("utf-8")).hexdigest()[:8]


def _construct_edibles(player: dict[str, Any]) -> list[dict[str, Any]]:
    """Съедобные предметы инвентаря: есть в каталоге с heal_hp и в запасе."""
    catalog = load_global_pool().get("items") or {}
    counts: dict[str, int] = {}
    for item in player.get("inventory", []):
        name = item.get("name")
        if name:
            counts[name] = counts.get(name, 0) + 1

    edibles = []
    for entry in catalog.values():
        heal = entry.get("heal_hp")
        name = entry.get("name")
        count = counts.get(name) if name else None
        if not heal or not name or not count:
            continue
        edibles.append({
            "name": name,
            "count": count,
            "heal_hp": int(heal),
            "hash": _item_hash(name),
        })
    edibles.sort(key=lambda e: e["name"])
    return edibles


def eat_inventory_item(user_id: int, name_hash: str) -> dict[str, Any]:
    """Атомарно слопать один экземпляр предмета инвентаря.

    Возвращает {"event": "eaten", item, healed, current, max_hp},
    {"event": "battle"} — во время отрыжки боя нельзя, или
    {"event": "empty"} — не съедобно / уже кончилось.
    """
    with _EXPEDITION_LOCK:
        data = load_player_data()
        player = data.get(_player_key(user_id))
        if not isinstance(player, dict):
            return {"event": "empty"}
        _ensure_player_defaults(player)

        if isinstance(player.get("active_battle"), dict):
            return {"event": "battle"}

        edibles = _construct_edibles(player)
        entry = next((e for e in edibles if e["hash"] == name_hash), None)
        if entry is None:
            return {"event": "empty"}

        inventory = player.get("inventory", [])
        for idx, item in enumerate(inventory):
            if item.get("name") == entry["name"]:
                del inventory[idx]
                break

        max_hp = int(player.get("max_hp", 200))
        before = int(player.get("hp", 0))
        healed = min(entry["heal_hp"], max_hp - before)
        player["hp"] = min(max_hp, before + entry["heal_hp"])
        save_player_data(data)
        return {
            "event": "eaten",
            "item": entry["name"],
            "healed": healed,
            "current": player["hp"],
            "max_hp": max_hp,
        }


def _equipped_summary(player: dict[str, Any]) -> str:
    """Короткая строка «что на гноме» для инвентаря/статистики (из equipment).

    Дублирования данных нет: строка собирается на лету из equipment, поэтому
    она всегда синхронна с кузней и со снапшотом боя.
    """
    equipped = _equipment_with_names(player)
    parts = []
    weapon = equipped.get("weapon")
    if weapon:
        parts.append(weapon.get("name", "?"))
    armor = [equipped[slot]["name"] for slot in ("head", "body", "dick") if slot in equipped]
    if armor:
        parts.extend(armor)
    trinket = equipped.get("trinket")
    if trinket:
        parts.append(f"🎪 {trinket.get('name', '?')}")
    return " · ".join(parts) or _get_text("adventure.inventory_equipped_none")


def _render_inventory(player: dict[str, Any]) -> str:
    """Текст инвентаря (общий для команды и для правок после укуса)."""
    inventory = player.get("inventory", [])
    lines = [
        _get_text("adventure.adventure_inventory_title"),
        _get_text("adventure.inventory_equipped_line", gear=_equipped_summary(player)),
        "",
    ]

    if inventory:
        inventory_counts: dict[str, int] = {}
        for item in inventory:
            item_name = item.get("name", "Unknown")
            inventory_counts[item_name] = inventory_counts.get(item_name, 0) + 1

        # Описания предметов из общего каталога (фрукты и прочие item_id).
        descriptions = {}
        for entry in (load_global_pool().get("items") or {}).values():
            if entry.get("description") and entry.get("name"):
                descriptions[entry["name"]] = entry["description"]

        for item_name, count in sorted(inventory_counts.items()):
            lines.append(_get_text("adventure.adventure_inventory_item", name=item_name, count=count))
            desc = descriptions.get(item_name)
            if desc:
                lines.append(_get_text("adventure.inventory_item_desc", desc=desc))
    else:
        lines.append(_get_text("adventure.adventure_inventory_no_items"))

    if _construct_edibles(player):
        lines.append("")
        lines.append(_get_text("adventure.inventory_edible_footer"))

    if _count_eggs(player):
        lines.append(_get_text("pets.egg_footer"))

    return "\n".join(lines)


def _count_eggs(player: dict[str, Any]) -> int:
    return sum(1 for item in player.get("inventory", []) if item.get("name") == PET_EGG_NAME)


def _inventory_keyboard(user_id: int, player: dict[str, Any]):
    rows = []
    if _construct_edibles(player):
        rows.append([InlineKeyboardButton(
            _get_text("adventure.inventory_use_button"),
            callback_data=f"invt_{user_id}_use",
        )])
    if _count_eggs(player):
        rows.append([InlineKeyboardButton(
            _get_text("pets.hatch_button"),
            callback_data=f"invt_{user_id}_hatch",
        )])
    return InlineKeyboardMarkup(rows) if rows else None


def _render_eat_panel(player: dict[str, Any]) -> str:
    return _get_text(
        "adventure.inventory_eat_panel",
        current=player.get("hp", 0),
        max_hp=player.get("max_hp", 200),
    )


def _eat_keyboard(user_id: int, edibles: list[dict[str, Any]]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            _get_text(
                "adventure.inventory_eat_button",
                item=e["name"], count=e["count"], hp=e["heal_hp"],
            ),
            callback_data=f"invt_{user_id}_eat_{e['hash']}",
        )]
        for e in edibles
    ]
    rows.append([InlineKeyboardButton(
        _get_text("adventure.inventory_eat_cancel"),
        callback_data=f"invt_{user_id}_cancel",
    )])
    return InlineKeyboardMarkup(rows)


async def adventure_inventory_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /adventure_inventory command - show player inventory."""
    user = update.effective_user
    if not user:
        return

    # Уборка: сама команда игрока и ответ бота живут минуту.
    await schedule_self_delete(context, update.effective_message)

    player = get_player(user.id)
    message = await update.effective_message.reply_text(
        _render_inventory(player),
        parse_mode=ParseMode.HTML,
        reply_markup=_inventory_keyboard(user.id, player),
    )
    await schedule_self_delete(context, message)


def _message_delete_args(update: Update) -> tuple[int, int] | None:
    """(chat_id, message_id) сообщения с кнопками пикера, если оно есть."""
    message = update.callback_query.message if update.callback_query else None
    if message is None:
        return None
    return message.chat_id, message.message_id


async def inventory_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Колбэки пикера поедания: invt_{owner}_{use|cancel} и invt_{owner}_eat_{hash}."""
    query = update.callback_query
    if query is None:
        return
    data = query.data or ""
    parts = data.split("_")  # invt_{owner}_{use|cancel} | invt_{owner}_eat_{hash}
    user = update.effective_user

    if user is None or len(parts) < 3 or parts[0] != "invt":
        await query.answer()
        return
    try:
        owner_id = int(parts[1])
    except ValueError:
        await query.answer()
        return
    if user.id != owner_id:
        await query.answer(_get_text("adventure.inventory_not_your"))
        return
    action = parts[2]

    player = get_player(user.id)
    message_args = _message_delete_args(update)

    if action == "use":
        edibles = _construct_edibles(player)
        if message_args:
            await redirect_self_delete(context, *message_args, EAT_SESSION_SECONDS)
        if not edibles:
            await query.answer(_get_text("adventure.inventory_eat_missing"))
            await query.edit_message_text(
                text=_render_inventory(player),
                parse_mode=ParseMode.HTML,
                reply_markup=None,
            )
            return
        await query.edit_message_text(
            text=_render_eat_panel(player),
            parse_mode=ParseMode.HTML,
            reply_markup=_eat_keyboard(user.id, edibles),
        )
        await query.answer()
        return

    if action == "cancel":
        await query.edit_message_text(
            text=_render_inventory(player),
            parse_mode=ParseMode.HTML,
            reply_markup=_inventory_keyboard(user.id, player),
        )
        if message_args:
            await redirect_self_delete(context, *message_args, DEFAULT_DELETE_DELAY)
        await query.answer()
        return

    if action == "hatch":
        # Ленивый импорт: handlers.pets импортирует этот модуль.
        from handlers.pets import hatch_pet

        result = hatch_pet(user.id)
        if result.get("event") == "empty":
            await query.answer(_get_text("pets.hatch_missing"))
            await query.edit_message_text(
                text=_render_inventory(player),
                parse_mode=ParseMode.HTML,
                reply_markup=_inventory_keyboard(user.id, player),
            )
            return

        player = get_player(user.id)
        lines = [
            get_text("pets.hatch_title"),
            get_text("pets.hatch_result", pet=result["pet_name"]),
        ]
        if result.get("replaced"):
            lines.append(get_text("pets.hatch_replaced", old=result["replaced"]))
        lines.append("")
        lines.append(f"<i>{result.get('flavor', '')}</i>")
        lines.extend(result.get("perk_lines") or [])
        await query.edit_message_text(
            text="\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=_inventory_keyboard(user.id, player),
        )
        await query.answer()
        return

    if action == "eat" and len(parts) == 4:
        result = eat_inventory_item(user.id, parts[3])
        if result.get("event") == "battle":
            await query.answer(_get_text("adventure.inventory_battle_busy"))
            return
        if result.get("event") != "eaten":
            await query.answer(_get_text("adventure.inventory_eat_missing"))
            return

        player = get_player(user.id)
        result_line = _get_text(
            "adventure.inventory_eat_result",
            item=result["item"],
            healed=result["healed"],
            current=result["current"],
            max_hp=result["max_hp"],
        )
        edibles = _construct_edibles(player)
        if edibles:
            text = result_line + "\n\n" + _render_eat_panel(player)
            reply_markup = _eat_keyboard(user.id, edibles)
        else:
            text = result_line + "\n\n" + _render_inventory(player)
            reply_markup = _inventory_keyboard(user.id, player)
        await query.edit_message_text(
            text=text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
        )
        if message_args:
            await redirect_self_delete(context, *message_args, EAT_SESSION_SECONDS)
        await query.answer()
        return

    await query.answer()


async def send_expedition_result(user_id: int, loc_id: str | None, username: str | None, context: ContextTypes.DEFAULT_TYPE, chat_id: int | None = None) -> None:
    """Finish the user's expedition (if still active) and send the report.

    Used by the job_queue completion callback and by the expiration checker.
    Safe to call multiple times: only the first caller resolves the
    expedition, later calls see no active expedition and do nothing.

    If an interactive battle is still running, the completion waits for it:
    the pending completion job is pushed back a minute and later calls retry.
    """
    player = get_player(user_id)
    if player.get("active_battle"):
        logger.info("Defer expedition result for %s: battle in progress", user_id)
        if context.job_queue:
            for job in context.job_queue.get_jobs_by_name(_expedition_job_name(user_id)):
                job.schedule_removal()
            context.job_queue.run_once(
                _expedition_complete_callback,
                60,
                name=_expedition_job_name(user_id),
                data={"user_id": user_id},
            )
        return

    result = finish_expedition(user_id, loc_id, username)
    if result is None:
        logger.info("Skip expedition result for %s: no active expedition", user_id)
        return

    # Send the report to the chat where the expedition was started.
    target_chat_id = result.get("chat_id") or chat_id or user_id
    logger.info(
        "Expedition result for %s (loc %s) -> chat %s",
        user_id, result.get("loc_id"), target_chat_id,
    )
    try:
        sent = await context.bot.send_message(
            chat_id=target_chat_id,
            text=result["text"],
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Error sending expedition completion to chat {target_chat_id}: {e}")
        return
    # Отбивка по итогу экспедиции живёт две минуты — итоги надо успеть прочитать.
    await schedule_self_delete(context, sent, EXPEDITION_RESULT_DELETE_DELAY)
