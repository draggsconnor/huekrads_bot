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
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, CommandHandler
from telegram.constants import ParseMode

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


def _roll_damage(base: int, variance: int = 3) -> int:
    return max(1, base + random.randint(-variance, variance))


# ---------------------------------------------------------------------------
# Combat system (from duel mechanics with zones and blocks)
# ---------------------------------------------------------------------------

def _resolve_encounter_fight(player_hp: int, player_max_hp: int, mob: dict[str, Any], player_level: int = 1) -> dict[str, Any]:
    """
    Turn-based fight for encounters. Uses zones and blocks.
    Returns dict with winner ('player' | 'mob'), player_hp_left, mob_hp_left,
    rounds list and encounter_result.
    """
    mob_hp = mob.get("hp", 30)
    mob_name = mob.get("name", "Моб")
    mob_dmg_base = mob.get("dmg", 5)
    mob_level = mob.get("level", 1)
    mob_tactics = mob.get("tactics", {})
    mob_debuff_chance = mob_tactics.get("debuff_chance", 0)
    mob_has_overpower = mob_tactics.get("overpower", False)  # like head_on_spider_legs
    
    player_max = player_max_hp
    current_player_hp = player_hp
    current_mob_hp = mob_hp
    rounds = []
    player_debuffed = False  # "Паранойя" debuff
    overpower_stacks = 0
    
    max_rounds = 10
    
    for r in range(1, max_rounds + 1):
        round_data = {"round": r}
        
        # Player's turn
        player_action = random.choice(["attack", "attack", "block"])  # 66% attack, 33% block
        if player_action == "block":
            round_data["player_action"] = "block"
            damage_reduction = 0.5
        else:
            round_data["player_action"] = "attack"
            damage_reduction = 1.0
        
        # Player attacks mob
        if player_action != "block":
            dmg_to_mob = _roll_damage(10 + 2 * max(1, player_level - 1), 3)
            current_mob_hp -= dmg_to_mob
            round_data["player_dmg"] = dmg_to_mob
        else:
            round_data["player_dmg"] = 0
        
        # Mob's turn
        if player_debuffed and random.random() < 0.3:
            # Player is stunned by paranoia
            round_data["player_stunned"] = True
            player_debuffed = False
        else:
            # Mob attacks player
            is_crit = random.random() < mob.get("crit_chance", 0.1)
            base_dmg = mob_dmg_base if not is_crit else mob_dmg_base * 2
            dmg_to_player = _roll_damage(int(base_dmg), 2)
            dmg_to_player = int(dmg_to_player * damage_reduction)
            current_player_hp -= dmg_to_player
            
            round_data["mob_dmg"] = dmg_to_player
            round_data["mob_crit"] = is_crit
        
        # Check for overpower (head_on_spider_legs special)
        if mob_has_overpower and mob_hp - current_mob_hp >= 15:
            overpower_stacks += 1
            if overpower_stacks >= 3:
                # Extra damage
                current_player_hp -= 5
                round_data["overpower_damage"] = 5
        
        rounds.append({
            "round": r,
            "player_action": round_data.get("player_action"),
            "player_dmg": round_data.get("player_dmg", 0),
            "mob_dmg": round_data.get("mob_dmg", 0),
            "player_hp": max(0, current_player_hp),
            "mob_hp": max(0, current_mob_hp),
            "player_stunned": round_data.get("player_stunned", False),
        })
        
        # Check debuff chance (bush_with_requirements)
        if mob_debuff_chance > 0 and random.random() < mob_debuff_chance:
            player_debuffed = True
            round_data["player_debuff"] = True
        
        if current_mob_hp <= 0:
            return {
                "winner": "player",
                "player_hp_left": max(0, current_player_hp),
                "mob_hp_left": 0,
                "rounds": rounds,
                "mob_name": mob_name,
                "encounter_result": "victory",
            }
        if current_player_hp <= 0:
            return {
                "winner": "mob",
                "player_hp_left": 0,
                "mob_hp_left": max(0, current_mob_hp),
                "rounds": rounds,
                "mob_name": mob_name,
                "encounter_result": "defeat",
            }
    
    # Draw -> mob escapes
    return {
        "winner": "draw",
        "player_hp_left": max(0, current_player_hp),
        "mob_hp_left": max(0, current_mob_hp),
        "rounds": rounds,
        "mob_name": mob_name,
        "encounter_result": "mob_escaped",
    }


# ---------------------------------------------------------------------------
# Loot helpers
# ---------------------------------------------------------------------------

def _roll_loot(loot_table: list[dict[str, Any]]) -> list[dict[str, Any]]:
    won = []
    for entry in loot_table:
        chance = entry.get("chance", 0.0)
        qty_min = entry.get("quantity_min", 1)
        qty_max = entry.get("quantity_max", 1)
        if random.random() < chance:
            qty = random.randint(qty_min, qty_max)
            won.append({**entry, "quantity": qty})
    return won


def _format_loot(loot: list[dict[str, Any]], pool: dict[str, Any]) -> str:
    lines = []
    for item in loot:
        item_id = item.get("item_id") or item.get("id")
        name = item.get("name")
        if not name and item_id and "items" in pool:
            name = pool["items"].get(item_id, {}).get("name", item_id)
        qty = item.get("quantity", 1)
        lines.append(f"• {name or item_id} x{qty}")
    return "\n".join(lines) if lines else get_text("adventure.no_loot")


# ---------------------------------------------------------------------------
# Expedition helpers
# ---------------------------------------------------------------------------

def _select_encounter_mob(loc_id: str, mobs_data: dict[str, Any], locations: dict[str, Any] | None = None) -> tuple[dict[str, Any], str | None]:
    """Select a random mob based on encounter_chance for the location.
    
    Returns tuple of (mob_data, mob_description) where:
    - mob_data: dict with mob stats for combat
    - mob_description: formatted description text from adventure.yaml
    """
    mob_name_text_key = None
    mob_desc_text_key = None
    
    if locations:
        loc = locations.get(loc_id, {})
        # Get specific_mobs as list of mob IDs from location
        specific_mob_ids = loc.get("specific_mobs", [])
        
        if specific_mob_ids:
            # Build weighted pool from specific mob IDs
            weighted_pool = []
            for mob_id in specific_mob_ids:
                mob_def = mobs_data.get("mobs", {}).get(mob_id, {})
                if mob_def:
                    encounter_chance = mob_def.get("encounter_chance", 1)
                    weighted_pool.append((mob_id, mob_def, encounter_chance))
            
            if weighted_pool:
                # Select mob based on weights
                total_weight = sum(w[2] for w in weighted_pool)
                rand = random.random() * total_weight
                cumulative = 0
                for mob_id, mob_def, weight in weighted_pool:
                    cumulative += weight
                    if rand <= cumulative:
                        # Return mob with its name and desc text keys
                        mob_name_text_key = f"{mob_id}_name"
                        mob_desc_text_key = f"{mob_id}_desc"
                        
                        mob_tactics = mob_def.get("tactics", {})
                        return (
                            {
                                "id": mob_id,
                                "name": mob_def.get("display_name", mob_id),
                                "hp": mob_def.get("hp", 30),
                                "max_hp": mob_def.get("hp", 30),
                                "attack": mob_def.get("attack", 5),
                                "defense": mob_def.get("defense", 1),
                                "speed": mob_def.get("speed", 3),
                                "crit_chance": mob_def.get("crit_chance", 0.0),
                                "dmg": mob_def.get("attack", 5),  # dmg from attack
                                "level": mob_def.get("min_level", 1),
                                "tactics": mob_tactics,
                                "special_ability": mob_def.get("special_ability"),
                                "debuff_chance": mob_tactics.get("debuff_chance", 0),
                                "overpower": mob_tactics.get("overpower", False),
                                "has_suck_ability": mob_id == "head_on_spider_legs",  # special for head mob
                                "turns_to_kill": mob_def.get("turns_to_kill", 3),  # for head_on_spider_legs
                            },
                            mob_desc_text_key
                        )
    
    # Fallback to generic mob generation if no specific mobs
    mob_types = mobs_data.get("mob_types", {})
    encounter_weights = mobs_data.get("encounter_weights", {})
    
    # Select rarity based on weights
    rand = random.random()
    rarity = "common"
    cumulative = 0
    for r, weight in encounter_weights.items():
        cumulative += weight
        if rand < cumulative:
            rarity = r
            break
    
    rarity_data = mob_types.get(rarity, {}).get("stats", {})
    if not rarity_data:
        return None, None
    
    # Generate random mob
    names = mob_types.get(rarity, {}).get("names", {}).get(loc_id, [f"{rarity.capitalize()} Mob"])
    name = random.choice(names)
    
    return (
        {
            "name": name,
            "hp": random.randint(rarity_data.get("hp", {}).get("min", 20), rarity_data.get("hp", {}).get("max", 50)),
            "attack": random.randint(rarity_data.get("attack", {}).get("min", 5), rarity_data.get("attack", {}).get("max", 10)),
            "defense": random.randint(rarity_data.get("defense", {}).get("min", 1), rarity_data.get("defense", {}).get("max", 3)),
            "speed": random.randint(rarity_data.get("speed", {}).get("min", 1), rarity_data.get("speed", {}).get("max", 5)),
            "crit_chance": rarity_data.get("crit_chance", {}).get("min", 0.05),
            "dmg": random.randint(rarity_data.get("attack", {}).get("min", 5), rarity_data.get("attack", {}).get("max", 10)),
            "level": 1,
            "tactics": {},
            "debuff_chance": 0,
            "overpower": False,
            "has_suck_ability": False,
            "turns_to_kill": 3,
        },
        None
    )


def _select_boss(loc: dict[str, Any]) -> dict[str, Any] | None:
    """Roll a boss encounter from the location's boss list (first hit wins).

    Returns a mob-style dict for _resolve_encounter_fight plus boss loot data.
    """
    for boss in loc.get("bosses", []) or []:
        try:
            chance = float(boss.get("encounter_chance", 0) or 0)
        except (TypeError, ValueError):
            continue
        if random.random() < chance:
            return {
                "name": boss.get("display_name", boss.get("name", _get_text("adventure.unknown_mob"))),
                "hp": boss.get("hp", 100),
                "dmg": boss.get("attack", 10),
                "crit_chance": boss.get("crit_chance", 0.1),
                "level": loc.get("level", 1),
                "tactics": {},
                "loot": boss.get("loot", []) or [],
                "base_reward": boss.get("base_reward", 0),
            }
    return None


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
    
    Job data should contain: user_id, loc_id, username, chat_id
    """
    job_data = context.job.data if context.job else None
    if not job_data:
        logger.error("No job data found for expedition completion")
        return
    
    user_id = job_data.get("user_id")
    loc_id = job_data.get("loc_id")
    username = job_data.get("username")
    chat_id = job_data.get("chat_id")  # chat_id where expedition was started
    
    if not user_id or not loc_id:
        logger.error(f"Invalid job data: {job_data}")
        return
    
    # Send expedition result
    await send_expedition_result(user_id, loc_id, username, context, chat_id)


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
        location_emoji=loc.get("display_name", "")[:2] if loc.get("display_name") else "🌲",
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

        # Снимаем запланированное завершение, если оно ещё в очереди.
        if context.job_queue:
            for job in context.job_queue.get_jobs_by_name(_expedition_job_name(user.id)):
                job.schedule_removal()

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

        expedition_data = {
            "location_id": loc_id,
            "location_name": loc.get("name", loc_id),
            "start_time": expedition_start.isoformat(),
            "end_time": expedition_end.isoformat(),
            "duration_seconds": duration_seconds,
            "chat_id": chat_id,
            "username": user.username or user.first_name,
        }

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
    """Atomically claim and resolve the player's active expedition.

    The expedition is cleared and all rewards are applied in a single write,
    so whoever fires first (the timer job, the expiration checker or the
    player's own click) is the only one to grant the rewards.

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

        locations = load_locations()
        loc = locations.get(loc_id, {})
        mobs_data = load_mobs()

        current_hp = player.get("hp", 200)
        max_hp = player.get("max_hp", 200)
        player_level = player.get("lvl", 1)

        # 1) Resources from the location's drop table.
        resources = _roll_resources(loc)

        # 2) Encounter (99%): a location boss may replace the regular mob.
        has_encounter = random.random() < 0.99
        encounter_result = None
        encounter_name = None
        is_boss_fight = False
        xp_gained = 0
        hp_healed = 0
        hp_after = current_hp

        if has_encounter:
            boss = _select_boss(loc)
            fight_result = None
            if boss is not None:
                is_boss_fight = True
                encounter_name = boss["name"]
                fight_result = _resolve_encounter_fight(current_hp, max_hp, boss, player_level)
            else:
                mob, mob_desc_key = _select_encounter_mob(loc_id, mobs_data, locations)
                if mob is not None:
                    if mob_desc_key:
                        encounter_name = _get_text(f"adventure.{mob_desc_key.replace('_desc', '_name')}")
                    encounter_name = encounter_name or mob.get("name") or _get_text("adventure.unknown_mob")
                    fight_result = _resolve_encounter_fight(current_hp, max_hp, mob, player_level)

            if fight_result is not None:
                encounter_result = fight_result["encounter_result"]
                hp_after = fight_result["player_hp_left"]
                if fight_result["winner"] == "player":
                    xp_gained += 10
                    if is_boss_fight:
                        xp_gained += int(boss.get("base_reward", 0) or 0)
                        for item in _roll_loot(boss.get("loot", []) or []):
                            resources.append({
                                "name": item.get("name") or item.get("item_id"),
                                "quantity": item.get("quantity", 1),
                            })
                    hp_healed = min(20, max_hp - hp_after)
                    hp_after += hp_healed
                else:
                    hp_after = max(1, hp_after - 5)

        # 3) Apply the outcome in the same write that claimed the expedition.
        new_hp = min(max_hp, hp_after)
        player["hp"] = new_hp
        player["xp"] = player.get("xp", 0) + xp_gained
        if resources:
            player["inventory"] = player.get("inventory", []) + resources
        player["total_expeditions"] = player.get("total_expeditions", 0) + 1
        if encounter_result is not None:
            player["total_fights"] = player.get("total_fights", 0) + 1
            if encounter_result == "victory":
                player["wins"] = player.get("wins", 0) + 1

        # 4) Level-ups: leftover XP carries over, each level raises max HP.
        leveled_up = False
        while player["xp"] >= _xp_for_next_level(player.get("lvl", 1)):
            player["xp"] -= _xp_for_next_level(player.get("lvl", 1))
            player["lvl"] = player.get("lvl", 1) + 1
            player["max_hp"] = 200 + (player["lvl"] - 1) * 20
            player["hp"] = min(player["max_hp"], player["hp"] + 20)
            leveled_up = True

        save_player_data(data)

    # 5) Build the report (pure formatting, no store access).
    result_lines = []
    if resources:
        res_list = "\n".join(_format_resource(r) for r in resources)
        result_lines.append(_get_text("adventure.expedition_resources", resources=res_list))
    else:
        result_lines.append(_get_text("adventure.expedition_no_resources"))

    encounters_summary = []
    if encounter_name:
        killed_key = "boss_killed" if is_boss_fight else "mob_killed"
        survived_key = "boss_survived" if is_boss_fight else "mob_survived"
        if encounter_result == "victory":
            encounters_summary.append(_get_mob_gendered_text(encounter_name, killed_key))
            if xp_gained > 0:
                encounters_summary.append(_get_text("adventure.reward_xp", xp=xp_gained))
            if hp_healed > 0:
                encounters_summary.append(_get_text("adventure.reward_hp", hp=hp_healed))
        else:
            # Поражение или ничья (враг сбежал) — враг уцелел.
            encounters_summary.append(_get_mob_gendered_text(encounter_name, survived_key))

    if encounter_result == "defeat":
        result_status = _get_text("adventure.expedition_result_fail")
    elif encounter_result == "victory" or resources:
        result_status = _get_text("adventure.expedition_result_success")
    else:
        result_status = _get_text("adventure.expedition_result_failure")

    summary_text = _get_text(
        "adventure.expedition_final_summary",
        username=username,
        hp=player["hp"],
        max_hp=player["max_hp"],
        total_xp=xp_gained,
        resources_summary="\n".join(result_lines),
        encounters_summary="\n".join(encounters_summary) if encounters_summary else "",
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
    
    await update.effective_message.reply_text(
        "\n".join(stats_lines),
        parse_mode=ParseMode.HTML
    )


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
    
    await update.effective_message.reply_text(
        "\n".join(inventory_lines),
        parse_mode=ParseMode.HTML
    )


async def send_expedition_result(user_id: int, loc_id: str | None, username: str | None, context: ContextTypes.DEFAULT_TYPE, chat_id: int | None = None) -> None:
    """Finish the user's expedition (if still active) and send the report.

    Used by the job_queue completion callback and by the expiration checker.
    Safe to call multiple times: only the first caller resolves the
    expedition, later calls see no active expedition and do nothing.
    """
    result = finish_expedition(user_id, loc_id, username)
    if result is None:
        logger.info("Skip expedition result for %s: no active expedition", user_id)
        return

    # Send the report to the chat where the expedition was started.
    target_chat_id = result.get("chat_id") or chat_id or user_id
    try:
        await context.bot.send_message(
            chat_id=target_chat_id,
            text=result["text"],
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Error sending expedition completion to chat {target_chat_id}: {e}")
