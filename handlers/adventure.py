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
from datetime import datetime, timedelta, timezone
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes, ConversationHandler

from text_resources import get_text

# Export for bot.py import

# States for interactive encounter fight
WAITING_FOR_PLAYER_ACTION = 1

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADVENTURE_DIR = os.path.join(BASE_DIR, "data", "adventure")

LOCATIONS_PATH = os.path.join(ADVENTURE_DIR, "locations.json")
GLOBAL_POOL_PATH = os.path.join(ADVENTURE_DIR, "global_pool.json")
PLAYER_DATA_PATH = os.path.join(ADVENTURE_DIR, "player_data.json")
MOBS_PATH = os.path.join(ADVENTURE_DIR, "mobs.json")


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def _load_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_json(path: str, data: Any) -> None:
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
    return _load_json(PLAYER_DATA_PATH)


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


def _roll_damage(base: int, variance: int = 3) -> int:
    return max(1, base + random.randint(-variance, variance))


# ---------------------------------------------------------------------------
# Combat system (from duel mechanics with zones and blocks)
# ---------------------------------------------------------------------------

def _resolve_encounter_fight(player_hp: int, player_max_hp: int, mob: dict[str, Any]) -> dict[str, Any]:
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
            dmg_to_mob = _roll_damage(10, 3)
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
                # Select mob based on weights - always encounter (100% chance)
                total_weight = sum(w[2] for w in weighted_pool)
                rand = random.random() * total_weight
                cumulative = 0
                for mob_id, mob_def, weight in weighted_pool:
                    # Use 1.0 for 100% encounter chance
                    cumulative += 1.0
                    if rand <= total_weight:  # Always select a mob
                        # Return mob with its name and desc text keys
                        mob_name_text_key = f"{mob_id}_name"
                        mob_desc_text_key = f"{mob_id}_desc"
                        
                        mob_tactics = mob_def.get("tactics", {})
                        # Return mob with 100% encounter chance
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
                                "encounter_chance": 1.0,  # 100% encounter chance
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


# ---------------------------------------------------------------------------
# Callback handlers
# ---------------------------------------------------------------------------

async def send_expedition_result(user_id: int, loc_id: str, username: str, context: ContextTypes.DEFAULT_TYPE, chat_id: int = None) -> None:
    """Send expedition result to player with encounter fight if applicable.
    
    After expedition, if the location has specific_mobs configured, always trigger
    an encounter fight (100% chance). Otherwise sends regular result.
    """
    # Get player and location data
    player = get_player(user_id)
    locations = load_locations()
    mobs_data = load_mobs()
    
    loc = locations.get(loc_id, {})
    loc_name = loc.get("name", loc_id)
    
    # Check if specific_mobs is configured for this location
    specific_mob_ids = loc.get("specific_mobs", [])
    
    if specific_mob_ids:
        # Location has specific mobs - ALWAYS trigger encounter (100% chance)
        # Select a random mob from the specific_mobs list
        mob_id = random.choice(specific_mob_ids)
        
        # Get mob data from mobs.json - search in specific_mobs[loc_id]
        specific_mobs = mobs_data.get("specific_mobs", {})
        mob_list = specific_mobs.get(loc_id, [])
        mob_data = None
        
        for mob in mob_list:
            if mob.get("id") == mob_id:
                mob_data = mob
                break
        
        if mob_data:
            # Start interactive encounter fight (100% encounter)
            await _start_interactive_expedition_encounter(user_id, mob_id, mob_data, username, context, chat_id, loc_id)
            return
    
    # No specific mob configured - regular expedition result (no encounter)
    await _send_regular_expedition_result(user_id, loc_id, username, context, chat_id)


async def _start_interactive_expedition_encounter(user_id: int, mob_id: str, mob_data: dict, username: str, context: ContextTypes.DEFAULT_TYPE, chat_id: int, loc_id: str) -> None:
    """Start interactive encounter fight after expedition.
    
    Creates a new message with fight interface if chat_id is provided.
    """
    # Get mob full data
    mobs_full = load_mobs()
    mob_full = mobs_full.get("specific_mobs", {}).get(loc_id, [])
    mob_def = None
    for m in mob_full:
        if m.get("id") == mob_id:
            mob_def = m
            break
    
    if not mob_def:
        # Fallback to mob_data
        mob_def = mob_data
    
    mob_hp = mob_def.get("hp", 30)
    player = get_player(user_id)
    player_hp = player.get("hp", 200)
    mob_name = mob_def.get("name", "Враг")
    
    # Initialize fight state
    player["active_encounter_fight"] = {
        "mob_data": mob_def,
        "current_player_hp": player_hp,
        "current_mob_hp": mob_hp,
        "rounds": [],
        "player_debuffed": False,
        "loc_id": loc_id,
    }
    save_player_data(player)
    
    # Build fight message
    message = (
        f"⚔️ <b>{username}</b>, экспедиция завершена!\n\n"
        f"📍 {loc_name}\n"
        f"💀 Встречен враг: {mob_name}\n\n"
        f"⚔️ <b>Начался бой!</b>\n\n"
        f"❤️ Ваше HP: {player_hp}\n"
        f"💀 HP врага: {mob_hp}\n\n"
        f"Выберите действие:"
    )
    
    keyboard = [
        [InlineKeyboardButton("⚔️ Атака", callback_data="encounter_attack"), InlineKeyboardButton("🛡️ Блок", callback_data="encounter_block")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    # Send message to chat where expedition was started
    if chat_id:
        await context.bot.send_message(
            chat_id=chat_id,
            text=message,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
    else:
        # Fallback to user's private chat
        await context.bot.send_message(
            chat_id=user_id,
            text=message,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
    
    logger.info(f"Interactive encounter fight started for user {user_id} with mob {mob_id}")


async def _send_regular_expedition_result(user_id: int, loc_id: str, username: str, context: ContextTypes.DEFAULT_TYPE, chat_id: int = None) -> None:
    """Send regular expedition result without encounter."""
    player = get_player(user_id)
    locations = load_locations()
    
    loc = locations.get(loc_id, {})
    loc_name = loc.get("name", loc_id)
    duration_minutes = loc.get("duration_minutes", 60)
    
    # Restore HP
    max_hp = player.get("max_hp", 200)
    hp_restored = max_hp - player.get("hp", 0)
    
    # Update player
    update_player(user_id, hp=max_hp, active_expedition=None)
    
    message = (
        f"✅ <b>{username}</b>, экспедиция завершена!\n\n"
        f"📍 {loc_name}\n"
        f"⏱️ Длительность: {duration_minutes} мин.\n\n"
        f"❤️ HP восстановлено: {hp_restored}\n"
        f"❤️ Текущее HP: {max_hp}/{max_hp}"
    )
    
    keyboard = [
        [InlineKeyboardButton("📍 К локациям", callback_data="adventure_locations")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if chat_id:
        await context.bot.send_message(
            chat_id=chat_id,
            text=message,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
    else:
        await context.bot.send_message(
            chat_id=user_id,
            text=message,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )


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
    username = job_data.get("username", "Гном")
    chat_id = job_data.get("chat_id")  # chat_id where expedition was started
    
    if not user_id or not loc_id:
        logger.error(f"Invalid job data: {job_data}")
        return
    
    # Send expedition result
    await send_expedition_result(user_id, loc_id, username, context, chat_id)


async def _cancel_expedition_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback для возврата гнома из экспедиции."""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    player = get_player(user_id)
    
    if not player.get("active_expedition"):
        await query.edit_message_text(_get_text("adventure.no_active_expedition"))
        return
    
    # Get username before clearing active_expedition
    user = query.from_user
    username = user.first_name if user else "Гном"
    if user.last_name:
        username = f"{username} {user.last_name}"
    
    # Restore HP to max
    old_hp = player["hp"]
    max_hp = player.get("max_hp", 200)
    hp_restored = max_hp - old_hp
    
    # Clear active_expedition
    update_player(user_id, active_expedition=None)
    
    # Get location info
    loc_id = player.get("active_expedition", {}).get("loc_id") if player.get("active_expedition") else "unknown"
    locations = load_locations()
    loc = locations.get(loc_id, {})
    loc_name = loc.get("name", loc_id)
    
    # Build response message
    message = (
        f"✅ <b>{username}</b> возвращён из экспедиции!\n\n"
        f"📍 Локация: {loc_name}\n"
        f"❤️ Восстановлено HP: {hp_restored} (текущее: {max_hp}/{max_hp})"
    )
    
    # Build keyboard with return to location list
    keyboard = [
        [InlineKeyboardButton(_get_text("adventure.back_to_locations"), callback_data="adventure_locations")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        text=message,
        reply_markup=reply_markup,
        parse_mode="HTML"
    )
    
    logger.info(f"Expedition cancelled for user {user_id} at {loc_id}")


async def start_expedition_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback для отправки гнома в экспедицию с кнопкой возврата."""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    player = get_player(user_id)
    
    # Check if already in expedition
    if player.get("active_expedition"):
        await query.edit_message_text(_get_text("adventure.already_in_expedition"))
        return
    
    # Extract loc_id from callback data (format: adventure_start_XXX)
    loc_id = query.data.replace("adventure_start_", "")
    
    # Get location info
    locations = load_locations()
    loc = locations.get(loc_id, {})
    if not loc:
        await query.edit_message_text(f"❌ Локация {loc_id} не найдена")
        return
    
    loc_name = loc.get("name", loc_id)
    duration_minutes = loc.get("duration_minutes", 60)
    duration_seconds = duration_minutes * 60
    
    user = query.from_user
    username = user.first_name if user else "Гном"
    if user.last_name:
        username = f"{username} {user.last_name}"
    
    # Set active expedition
    expedition_data = {
        "loc_id": loc_id,
        "loc_name": loc_name,
        "start_time": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": duration_seconds,
    }
    update_player(user_id, active_expedition=expedition_data)
    
    # Schedule completion callback
    job_queue = context.job_queue
    job_queue.run_once(
        _expedition_complete_callback,
        duration_seconds,
        data={"user_id": user_id, "loc_id": loc_id, "username": username, "chat_id": query.message.chat_id}
    )
    
    # Build response with cancel button
    keyboard = [
        [InlineKeyboardButton("🔄 Вернуть из экспедиции", callback_data="adventure_cancel_expedition")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message = (
        f"🗺️ <b>{username}</b> отправляется в экспедицию!\n\n"
        f"📍 Локация: {loc_name}\n"
        f"⏱️ Длительность: {duration_minutes} мин.\n\n"
        f"Используйте кнопку ниже чтобы вернуть гнома досрочно."
    )
    
    await query.edit_message_text(
        text=message,
        reply_markup=reply_markup,
        parse_mode="HTML"
    )
    
    logger.info(f"Expedition started for user {user_id} at {loc_id}")


# ---------------------------------------------------------------------------
# Interactive Encounter Fight
# ---------------------------------------------------------------------------

def _create_encounter_fight_keyboard(player_action: str = None) -> InlineKeyboardMarkup:
    """Create inline keyboard for interactive encounter fight."""
    if player_action == "attack":
        keyboard = [
            [InlineKeyboardButton("⚔️ Атака", callback_data="encounter_attack"), InlineKeyboardButton("🛡️ Блок", callback_data="encounter_block")],
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("⚔️ Атака", callback_data="encounter_attack"), InlineKeyboardButton("🛡️ Блок", callback_data="encounter_block")],
        ]
    return InlineKeyboardMarkup(keyboard)


async def _handle_encounter_fight_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle interactive encounter fight callbacks."""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    player = get_player(user_id)
    
    # Get active encounter fight state
    encounter_state = player.get("active_encounter_fight")
    
    if not encounter_state:
        await query.edit_message_text("⚠️ Бой не активен")
        return ConversationHandler.END
    
    mob_data = encounter_state.get("mob_data", {})
    current_player_hp = encounter_state.get("current_player_hp", player["hp"])
    current_mob_hp = encounter_state.get("current_mob_hp", mob_data.get("hp", 30))
    rounds = encounter_state.get("rounds", [])
    mob_name = mob_data.get("name", "Враг")
    player_debuffed = encounter_state.get("player_debuffed", False)
    
    # Check if fight is already over
    if current_mob_hp <= 0 or current_player_hp <= 0:
        await query.edit_message_text("⚔️ Бой уже завершен")
        return ConversationHandler.END
    
    # Get player action from callback
    player_action = query.data
    
    # Player's turn
    if player_action == "encounter_attack":
        # Player attacks
        from handlers.adventure import _roll_damage
        dmg_to_mob = _roll_damage(10, 3)
        current_mob_hp -= dmg_to_mob
        
        round_data = {
            "round": len(rounds) + 1,
            "player_action": "attack",
            "player_dmg": dmg_to_mob,
        }
    elif player_action == "encounter_block":
        # Player blocks - reduce damage by 50%
        round_data = {
            "round": len(rounds) + 1,
            "player_action": "block",
            "player_dmg": 0,
        }
        damage_reduction = 0.5
    else:
        await query.edit_message_text("⚠️ Неверное действие")
        return ConversationHandler.END
    
    # Mob's turn
    if current_mob_hp > 0:
        # Check if player is stunned from debuff
        if player_debuffed and random.random() < 0.3:
            round_data["player_stunned"] = True
            player_debuffed = False
        else:
            # Mob attacks
            mob_dmg_base = mob_data.get("dmg", 5)
            is_crit = random.random() < mob_data.get("crit_chance", 0.1)
            base_dmg = mob_dmg_base * 2 if is_crit else mob_dmg_base
            mob_dmg = _roll_damage(int(base_dmg), 2)
            
            if player_action == "encounter_block":
                mob_dmg = int(mob_dmg * damage_reduction)
            
            current_player_hp -= mob_dmg
            round_data["mob_dmg"] = mob_dmg
            round_data["mob_crit"] = is_crit
    
    rounds.append(round_data)
    
    # Update state
    player["active_encounter_fight"] = {
        "mob_data": mob_data,
        "current_player_hp": current_player_hp,
        "current_mob_hp": current_mob_hp,
        "rounds": rounds,
        "player_debuffed": player_debuffed,
    }
    save_player_data(player)
    
    # Build fight message
    player_hp_display = max(0, current_player_hp)
    mob_hp_display = max(0, current_mob_hp)
    mob_action = round_data.get("mob_dmg", 0)
    mob_crit = round_data.get("mob_crit", False)
    player_stunned = round_data.get("player_stunned", False)
    
    if player_action == "encounter_attack":
        attack_text = f"✅ Ваш удар! {dmg_to_mob} урона по {mob_name}"
    else:
        attack_text = "🛡️ Вы блокируете атаку"
    
    if player_stunned:
        attack_text += " ⚡(Паранойя: пропуск хода!)"
    
    if mob_action > 0:
        mob_attack_text = f"⚔️ {mob_name} атакует: {mob_action} урона"
        if mob_crit:
            mob_attack_text += " 💣КРИТ!"
    else:
        mob_attack_text = f"🛡️ {mob_name} промахивается"
    
    message = (
        f"⚔️ <b>Бой с {mob_name}</b>\n\n"
        f"{attack_text}\n"
        f"{mob_attack_text}\n\n"
        f"❤️ Ваше HP: {player_hp_display}\n"
        f"💀 HP врага: {mob_hp_display}"
    )
    
    # Check for win/loss
    if current_mob_hp <= 0:
        # Victory
        player["active_encounter_fight"] = None
        save_player_data(player)
        
        xp_gained = int(mob_data.get("xp_reward", 50))
        gold_gained = int(mob_data.get("gold_reward", 20))
        
        # Update player stats
        new_xp = player.get("xp", 0) + xp_gained
        new_gold = player.get("gold", 0) + gold_gained
        new_level = player.get("lvl", 1)
        new_total_fights = player.get("total_fights", 0) + 1
        new_wins = player.get("wins", 0) + 1
        
        # Check level up
        xp_to_next = new_level * 100
        level_up = False
        new_max_hp = player.get("max_hp", 200)
        if new_xp >= xp_to_next:
            new_xp -= xp_to_next
            new_level += 1
            level_up = True
            new_max_hp = player.get("max_hp", 200) + 20
        
        update_player(
            user_id,
            hp=player_hp_display,
            xp=new_xp,
            gold=new_gold,
            lvl=new_level,
            max_hp=new_max_hp,
            total_fights=new_total_fights,
            wins=new_wins,
        )
        
        victory_msg = (
            f"🏆 <b>ПОБЕДА!</b>\n\n"
            f"Вы одолели {mob_name}!\n\n"
            f"⭐ Опыт: +{xp_gained}\n"
            f"💰 Золото: +{gold_gained}"
        )
        if level_up:
            victory_msg += f"\n\n⭐ <b>НОВЫЙ УРОВЕНЬ! {new_level}</b>"
        
        keyboard = [
            [InlineKeyboardButton(_get_text("adventure.back_to_locations"), callback_data="adventure_locations")],
        ]
        await query.edit_message_text(
            text=victory_msg,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        return ConversationHandler.END
    
    elif current_player_hp <= 0:
        # Defeat
        player["active_encounter_fight"] = None
        save_player_data(player)
        
        update_player(user_id, hp=0)
        
        defeat_msg = (
            f"💀 <b>ПОРАЖЕНИЕ</b>\n\n"
            f"{mob_name} оказался сильнее.\n\n"
            f"Попробуйте еще раз!"
        )
        
        keyboard = [
            [InlineKeyboardButton(_get_text("adventure.back_to_locations"), callback_data="adventure_locations")],
        ]
        await query.edit_message_text(
            text=defeat_msg,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="HTML"
        )
        return ConversationHandler.END
    
    # Continue fight
    await query.edit_message_text(
        text=message,
        reply_markup=_create_encounter_fight_keyboard(player_action),
        parse_mode="HTML"
    )
    
    return WAITING_FOR_PLAYER_ACTION


async def start_interactive_encounter_fight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start an interactive encounter fight."""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    player = get_player(user_id)
    
    # Get encounter data from callback
    parts = query.data.split("_")
    if len(parts) < 4:
        await query.edit_message_text("❌ Неверные данные боя")
        return ConversationHandler.END
    
    mob_id = parts[3]
    mob_data = load_mobs().get("mobs", {}).get(mob_id, {})
    
    if not mob_data:
        await query.edit_message_text("❌ Моб не найден")
        return ConversationHandler.END
    
    # Initialize fight state
    mob_hp = mob_data.get("hp", 30)
    player_hp = player["hp"]
    
    player["active_encounter_fight"] = {
        "mob_data": mob_data,
        "current_player_hp": player_hp,
        "current_mob_hp": mob_hp,
        "rounds": [],
        "player_debuffed": False,
    }
    save_player_data(player)
    
    # Build initial fight message
    mob_name = mob_data.get("display_name", mob_id)
    message = (
        f"⚔️ <b>Начался бой!</b>\n\n"
        f"Противник: {mob_name}\n"
        f"❤️ Ваше HP: {player_hp}\n"
        f"💀 HP врага: {mob_hp}\n\n"
        f"Выберите действие:"
    )
    
    keyboard = [
        [InlineKeyboardButton("⚔️ Атака", callback_data="encounter_attack"), InlineKeyboardButton("🛡️ Блок", callback_data="encounter_block")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        text=message,
        reply_markup=reply_markup,
        parse_mode="HTML"
    )
    
    return WAITING_FOR_PLAYER_ACTION


async def _show_fight_details_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback to show detailed fight breakdown."""
    query = update.callback_query
    await query.answer()
    
    # Parse callback data: adventure_fight_details_{user_id}_{loc_id}
    parts = query.data.split("_")
    if len(parts) < 5:
        await query.edit_message_text("❌ Неверные данные боя")
        return
    
    user_id = int(parts[3])
    loc_id = parts[4]
    
    # Check if user requesting their own fight details
    if query.from_user.id != user_id:
        await query.edit_message_text("❌ Это не ваши детали боя")
        return
    
    # Get fight details from player data
    player = get_player(user_id)
    fight_details = player.get("last_fight_details")
    
    if not fight_details:
        await query.edit_message_text("❌ Детали боя не найдены")
        return
    
    # Get location name
    locations = load_locations()
    loc = locations.get(loc_id, {})
    loc_name = loc.get("name", loc_id)
    
    # Build details message
    mob_name = fight_details.get("mob_name", "Неизвестный")
    encounter_result = fight_details.get("encounter_result")
    rounds = fight_details.get("rounds", [])
    
    result_text = {
        "victory": "🎉 <b>ПОБЕДА!</b> Вы одолели противника.",
        "defeat": "💀 <b>ПОРАЖЕНИЕ</b> Противник оказался сильнее.",
        "mob_escaped": "🏃 <b>НИЧЬЯ!</b> Противник скрылся."
    }.get(encounter_result, "Бой завершён")
    
    lines = [
        f"⚔️ <b>Детали боя</b>",
        f"",
        f"📍 Локация: {loc_name}",
        f"⚔️ Противник: {mob_name}",
        f"",
        f"{result_text}",
        f"",
    ]
    
    for rnd in rounds:
        player_hp = rnd.get("player_hp", 0)
        mob_hp = rnd.get("mob_hp", 0)
        player_dmg = rnd.get("player_dmg", 0)
        
        player_action = rnd.get("player_action", "block")
        action_text = "🛡️ Блок" if player_action == "block" else f"⚔️ Атака ({player_dmg} урона)"
        
        stun_text = " ⚡Ошеломлен!" if rnd.get("player_stunned") else ""
        
        lines.append(f"  Ход {rnd['round']}: {action_text}{stun_text}")
        lines.append(f"  Ваш HP: {player_hp}, HP врага: {mob_hp}")
    
    lines.append("")
    lines.append("🔄 Нажмите кнопку ниже для возврата")
    
    keyboard = [
        [InlineKeyboardButton("📍 К локациям", callback_data="adventure_locations")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(
        text="\n".join(lines),
        reply_markup=reply_markup,
        parse_mode="HTML"
    )


def _format_fight_details(fight_result: dict, mob_data: dict) -> str:
    """Format detailed fight breakdown for display."""
    lines = [
        f"⚔️ <b>Детали боя</b>",
        f"",
        f"👤 <b>Вы</b> vs {mob_data.get('name', 'Врагом')}",
        f"",
    ]
    
    for rnd in fight_result["rounds"]:
        player_hp = rnd.get("player_hp", 0)
        mob_hp = rnd.get("mob_hp", 0)
        player_dmg = rnd.get("player_dmg", 0)
        mob_dmg = rnd.get("mob_dmg", 0)
        
        player_action = rnd.get("player_action", "block")
        action_text = "🛡️ Блок" if player_action == "block" else f"⚔️ Атака ({player_dmg} урона)"
        
        stun_text = " (⚡ Ошеломлен!)" if rnd.get("player_stunned") else ""
        crit_text = " (💥 КРИТ!)" if rnd.get("mob_crit") else ""
        
        lines.append(f"  Ход {rnd['round']}: {action_text}{stun_text}")
        lines.append(f"  Ваш HP: {player_hp}, HP врага: {mob_hp}{crit_text}")
        lines.append("")
    
    winner = fight_result["winner"]
    if winner == "player":
        lines.append("🎉 <b>Победа!</b> Вы одолели противника.")
    elif winner == "mob":
        lines.append("💀 <b>Поражение</b> Противник оказался сильнее.")
    else:
        lines.append("🏃 <b>Ничья!</b> Противник скрылся.")
    
    return "\n".join(lines)


def register_adventure_handlers(application) -> None:
    """Register adventure handlers with the application."""
    application.add_handler(CallbackQueryHandler(start_expedition_callback, pattern=r"^adventure_start_"))
    application.add_handler(CallbackQueryHandler(_cancel_expedition_callback, pattern="adventure_cancel_expedition"))
    application.add_handler(CallbackQueryHandler(_handle_encounter_fight_callback, pattern=r"^encounter_"))
    application.add_handler(CallbackQueryHandler(_show_fight_details_callback, pattern=r"^adventure_fight_details_"))
    
    # Register interactive encounter fight conversation handler
    encounter_fight_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_interactive_encounter_fight, pattern=r"^encounter_start_")],
        states={
            WAITING_FOR_PLAYER_ACTION: [CallbackQueryHandler(_handle_encounter_fight_callback, pattern=r"^encounter_")],
        },
        fallbacks=[CallbackQueryHandler(_show_fight_details_callback, pattern=r"^adventure_fight_details_")],
    )
    application.add_handler(encounter_fight_handler)


async def adventure_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Main adventure command handler - shows location selection."""
    query = update.callback_query if update.callback_query else None
    chat_id = update.effective_chat.id if update.effective_chat else None
    
    if query:
        await query.answer()
    
    locations = load_locations()
    text = _get_text("adventure.select_location")
    
    keyboard = []
    for loc_id, loc in locations.items():
        if loc.get("enabled", True):
            loc_name = loc.get("name", loc_id)
            duration = loc.get("duration_minutes", 60)
            keyboard.append([InlineKeyboardButton(
                f"{loc_name} ({duration} мин)",
                callback_data=f"adventure_start_{loc_id}"
            )])
    
    keyboard.append([InlineKeyboardButton(_get_text("adventure.back"), callback_data="main_menu")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if query:
        await query.edit_message_text(
            text=text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )
    elif chat_id:
        await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )


# Export for bot.py import
adventure_command = adventure_handler
