"""
Adventure module: turn-based PvE fights at various locations.
Uses:
  data/adventure/locations.json   — location definitions & loot tables
  data/adventure/global_pool.json — global item pool references
  data/adventure/player_data.json — per-player HP / cooldowns / stats
  data/adventure/mobs.json        — mob definitions for encounters
  texts/adventure.yaml            — user-facing texts
"""

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

from handlers.utils import schedule_self_delete
from text_resources import get_text, get_text_list

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADVENTURE_DIR = os.path.join(BASE_DIR, "data", "adventure")
PERSIST_DIR = os.path.join(BASE_DIR, "persist")

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
    """XP needed to advance from the given level (lvl 1 -> 2 costs 100)."""
    return 50 + level * 50


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
    return InlineKeyboardMarkup(buttons)


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

    return _get_text(
        "adventure.location_detailed_info",
        username=user.username or user.first_name,
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

async def adventure_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Entry point: /adventure — show available locations."""
    locations = load_locations()
    text = _get_text("adventure.welcome")
    await update.effective_message.reply_text(
        text, reply_markup=_locations_keyboard(locations), parse_mode='HTML'
    )


async def adventure_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle all adventure inline callbacks."""
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    user = update.effective_user
    if not user:
        return

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
            username=user.username or user.first_name,
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


def _roll_resources(loc: dict[str, Any]) -> list[dict[str, Any]]:
    """Roll the location's resource drop table."""
    resources = []
    for resource in loc.get("resources", []) or []:
        if random.random() < resource.get("chance", 0):
            resources.append({
                "name": resource.get("name", resource.get("id", "Unknown")),
                "quantity": 1,
            })
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
        f"👤 <b>{username}</b>",
        "",
        _get_text("adventure.adventure_stats_level", level=level),
        _get_text("adventure.adventure_stats_hp", current=current_hp, max=max_hp),
        _get_text("adventure.adventure_stats_gold", gold=gold),
        _get_text("adventure.adventure_stats_xp", xp=xp),
        _get_text("adventure.adventure_stats_expeditions", count=total_expeditions),
        _get_text("adventure.adventure_stats_expeditions_success", success=wins),
    ]
    
    if failed > 0:
        stats_lines.append(_get_text("adventure.adventure_stats_expeditions_failed", failed=failed))
    
    # Уборка: сама команда игрока и ответ бота живут минуту.
    await schedule_self_delete(context, update.effective_message)
    message = await update.effective_message.reply_text(
        "\n".join(stats_lines),
        parse_mode=ParseMode.HTML
    )
    await schedule_self_delete(context, message)


async def adventure_inventory_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /adventure_inventory command - show player inventory."""
    user = update.effective_user
    if not user:
        return
    
    player = get_player(user.id)
    inventory = player.get("inventory", [])
    
    # Build inventory message
    inventory_lines = [
        _get_text("adventure.adventure_inventory_title"),
        ""
    ]
    
    if inventory:
        # Group inventory items by name
        inventory_counts = {}
        for item in inventory:
            item_name = item.get("name", "Unknown")
            inventory_counts[item_name] = inventory_counts.get(item_name, 0) + 1
        
        for item_name, count in sorted(inventory_counts.items()):
            inventory_lines.append(_get_text("adventure.adventure_inventory_item", name=item_name, count=count))
    else:
        inventory_lines.append(_get_text("adventure.adventure_inventory_no_items"))
    
    # Уборка: сама команда игрока и ответ бота живут минуту.
    await schedule_self_delete(context, update.effective_message)
    message = await update.effective_message.reply_text(
        "\n".join(inventory_lines),
        parse_mode=ParseMode.HTML
    )
    await schedule_self_delete(context, message)


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
    try:
        sent = await context.bot.send_message(
            chat_id=target_chat_id,
            text=result["text"],
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Error sending expedition completion to chat {target_chat_id}: {e}")
        return
    # Отбивка по итогу экспедиции видна минуту, потом бот её убирает.
    await schedule_self_delete(context, sent)
