import asyncio
import copy
import json
import logging
import random
import time
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

import config
from handlers.adventure_expiration_checker import check_expired_adventures

from .utils import send_reply, strip_html_tags

logger = logging.getLogger(__name__)

# File paths for adventure module data
ADVENTURE_FILES = {
    "locations": config.ADVENTURE_DIR / "locations.json",
    "mobs": config.ADVENTURE_DIR / "mobs.json",
    "player_data": config.ADVENTURE_DIR / "player_data.json",
    "global_pool": config.ADVENTURE_DIR / "global_pool.json",
}


def load_data(filepath):
    """Load JSON data from a file. Returns dict or list depending on file content."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_data(filepath, data):
    """Save JSON data to a file."""
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════
# INITIALIZATION
# ═══════════════════════════════════════════════════════


def get_adventure_mobs() -> dict:
    """Load mobs data from JSON."""
    return load_data(ADVENTURE_FILES["mobs"])


def get_locations() -> dict:
    """Load locations data from JSON."""
    return load_data(ADVENTURE_FILES["locations"])


def get_player_data() -> dict:
    """Load per-player adventure data from JSON."""
    return load_data(ADVENTURE_FILES["player_data"])


def get_global_pool() -> list:
    """Load global pool of mob kills from JSON."""
    return load_data(ADVENTURE_FILES["global_pool"])


def save_player_data(data: dict):
    """Save per-player adventure data to JSON."""
    save_data(ADVENTURE_FILES["player_data"], data)


def save_global_pool(data: list):
    """Save global pool of mob kills to JSON."""
    save_data(ADVENTURE_FILES["global_pool"], data)


# ═══════════════════════════════════════════════════════
# MOB SELECTION
# ═══════════════════════════════════════════════════════


def get_mobs_for_location(location_id: str, player_data: dict, current_time: float) -> list:
    """Determine which mobs are active for a given location based on time of day."""
    mobs_data = get_adventure_mobs()
    all_mobs = mobs_data.get(location_id, [])
    current_hour = datetime.fromtimestamp(current_time, tz=timezone.utc).hour + 3  # UTC+3
    active_mobs = []
    for mob in all_mobs:
        mob_copy = copy.deepcopy(mob)
        # Determine display name based on time of day
        if current_hour >= 22 or current_hour < 6:
            mob_copy["display_name"] = mob.get("night_name", mob["name"])
        elif 6 <= current_hour < 10:
            mob_copy["display_name"] = mob.get("morning_name", mob["name"])
        elif 12 <= current_hour < 14:
            mob_copy["display_name"] = mob.get("day_name", mob["name"])
        elif 18 <= current_hour < 22:
            mob_copy["display_name"] = mob.get("evening_name", mob["name"])
        else:
            mob_copy["display_name"] = mob["name"]
        # Apply spawn time restrictions
        spawn_time = mob.get("spawn_time")
        if spawn_time:
            if spawn_time == "night" and not (current_hour >= 22 or current_hour < 6):
                continue
            if spawn_time == "day" and (current_hour >= 22 or current_hour < 6):
                continue
        active_mobs.append(mob_copy)
    return active_mobs


def choose_mob(location_id: str, player_data: dict, current_time: float) -> dict:
    """Choose a mob based on spawn chance, degrade its armor if present, and return a deep copy."""
    active_mobs = get_mobs_for_location(location_id, player_data, current_time)
    if not active_mobs:
        return None
    # Build weighted list
    weighted = []
    for mob in active_mobs:
        chance = mob.get("spawn_chance", 1)
        weighted.extend([mob] * chance)
    chosen = copy.deepcopy(random.choice(weighted))
    # Degrade armor durability based on global pool size
    if "armor" in chosen:
        pool = get_global_pool()
        for armor_piece in chosen["armor"]:
            max_durability = armor_piece.get("durability", 100)
            degradation = min(len(pool) * 2, max_durability - 1)
            armor_piece["durability"] = max(1, max_durability - degradation)
    return chosen


# ═══════════════════════════════════════════════════════
# DAMAGE CALCULATION
# ═══════════════════════════════════════════════════════


def calculate_player_damage(player_attack: int, mob: dict) -> int:
    """Calculate the damage the player deals. Armor reduces damage (durability cap at 80)."""
    base_damage = float(max(1, player_attack - mob.get("defense", 0)))
    armor_durability = sum(a.get("durability", 0) for a in mob.get("armor", []))
    armor_reduction = min(armor_durability * 0.5, 80)
    damage = max(1, int(base_damage - armor_reduction))
    return damage


def calculate_mob_damage(mob: dict, player_data: dict) -> int:
    """Calculate the damage the mob deals. Player armor reduces damage (durability cap at 80)."""
    base_damage = float(max(1, mob.get("attack", 1) - player_data.get("defense", 0)))
    player_armor = player_data.get("armor", [])
    armor_durability = sum(a.get("durability", 0) for a in player_armor)
    armor_reduction = min(armor_durability * 0.5, 80)
    damage = max(1, int(base_damage - armor_reduction))
    return damage


# ═══════════════════════════════════════════════════════
# REWARD DISTRIBUTION
# ═══════════════════════════════════════════════════════


def distribute_loot(mob: dict) -> dict:
    """Process mob drops: inventory items and direct rewards."""
    drops = {"items": [], "gold": 0, "experience": mob.get("experience", 0)}
    loot_table = mob.get("drops", [])
    for item in loot_table:
        if random.random() < item.get("chance", 0):
            quantity = random.randint(item.get("min", 1), item.get("max", 1))
            drops["items"].append({"name": item["name"], "quantity": quantity})
            drops["gold"] += item.get("gold", 0) * quantity
        else:
            drops["gold"] += item.get("gold_fail", 0)
    return drops


def distribute_rewards(mob: dict, player_data: dict, user_id: str) -> dict:
    """Handle gold, experience, and item drops."""
    rewards = distribute_loot(mob)
    rewards["base_gold"] = mob.get("gold", 0)
    rewards["total_gold"] = rewards["gold"] + rewards["base_gold"]
    player_data["gold"] = player_data.get("gold", 0) + rewards["total_gold"]
    player_data["experience"] = player_data.get("experience", 0) + rewards["experience"]
    return rewards


# ═══════════════════════════════════════════════════════
# COMBAT FLOW
# ═══════════════════════════════════════════════════════


def _build_adventure_text(player_name: str, mob: dict, turn_messages: list,
                          rewards: dict = None, is_running: bool = False) -> str:
    """Construct the multi-line adventure message with HTML formatting."""
    full_mob_name = mob.get("display_name", mob["name"])
    lines = [f"⚔️ <b>{player_name}</b> vs <b>{full_mob_name}</b>\n"]
    if not turn_messages:
        lines.append("🤔 Ожидание действия...")
    else:
        lines.extend(turn_messages)
    if is_running:
        lines.append("\n🏃‍♂️ <b>Ты сбежал!</b>")
    elif rewards:
        lines.append("\n🎉 <b>Победа!</b>")
        lines.append(f"💰 Золота: +{rewards['total_gold']}")
        lines.append(f"✨ Опыта: +{rewards['experience']}")
        if rewards.get("items"):
            lines.append("📦 Добыча:")
            for drop in rewards["items"]:
                lines.append(f"  • {drop['name']} × {drop['quantity']}")
    footer = "\n\n" + "─" * 20
    return "\n".join(lines) + footer


def build_combat_keyboard(has_mob: bool = True, in_progress: bool = True) -> InlineKeyboardMarkup:
    """Build the inline keyboard for combat actions."""
    buttons = []
    if has_mob and in_progress:
        buttons = [
            [
                InlineKeyboardButton("🗡️ Атаковать", callback_data="combat.attack"),
                InlineKeyboardButton("🏃 Сбежать", callback_data="combat.run"),
            ]
        ]
    else:
        # Fallback keyboard when no mob is present (should be removed in real flow)
        buttons = [
            [InlineKeyboardButton("🔙 Назад", callback_data="adventure.back")],
        ]
    return InlineKeyboardMarkup(buttons)


# ═══════════════════════════════════════════════════════
# MAIN ADVENTURE LOGIC
# ═══════════════════════════════════════════════════════


def create_adventure_session(user_id: str, location_id: str, location_data: dict) -> dict:
    """Initialize a new combat session for the user."""
    current_time = time.time()
    player_data = get_player_data()
    user_entry = player_data.setdefault(user_id, {})
    mob = choose_mob(location_id, player_data, current_time)
    if not mob:
        return None
    session = {
        "location_id": location_id,
        "location_name": location_data.get("name", location_id),
        "duration": location_data.get("duration_minutes", 5) * 60,
        "start_time": current_time,
        "mob": mob,
        "mob_hp": mob.get("health", 100),
        "turn_messages": [
            f"Ты встретил <b>{mob.get('display_name', mob['name'])}</b>!",
            f"❤️ Здоровье врага: {mob.get('health', 100)}",
        ],
        "player_data": user_entry,
        "in_combat": True,
        "running": False,
        "finished": False,
        "rewards": None,
    }
    # Set defaults for player stats
    user_entry.setdefault("attack", 10)
    user_entry.setdefault("defense", 0)
    user_entry.setdefault("health", 100)
    user_entry.setdefault("gold", 0)
    user_entry.setdefault("experience", 0)
    user_entry.setdefault("inventory", [])
    user_entry.setdefault("armor", [])
    return session


def process_attack(session: dict, user_id: str) -> dict:
    """Execute an attack: calculate damage, update HP, handle death, return updated session."""
    mob = session["mob"]
    player_data = session["player_data"]
    player_attack = player_data.get("attack", 10)
    # Player hits mob
    p_damage = calculate_player_damage(player_attack, mob)
    session["mob_hp"] -= p_damage
    turn_log = [f"🗡️ Ты нанёс <b>{p_damage}</b> урона."]
    if session["mob_hp"] <= 0:
        # Mob dies
        rewards = distribute_rewards(mob, player_data, user_id)
        session["rewards"] = rewards
        session["in_combat"] = False
        session["finished"] = True
        turn_log.append("💀 Враг повержен!")
        turn_log.append(f"💰 Получено золота: +{rewards['total_gold']}")
        turn_log.append(f"✨ Получено опыта: +{rewards['experience']}")
        if rewards.get("items"):
            turn_log.append("📦 Добыча:")
            for drop in rewards["items"]:
                turn_log.append(f"  • {drop['name']} × {drop['quantity']}")
        # Update player inventory
        for drop in rewards["items"]:
            inv = player_data.setdefault("inventory", [])
            existing = next((i for i in inv if i["name"] == drop["name"]), None)
            if existing:
                existing["quantity"] += drop["quantity"]
            else:
                inv.append({"name": drop["name"], "quantity": drop["quantity"]})
        # Grant armor from mob drops to player
        for armor_piece in mob.get("armor", []):
            inv = player_data.setdefault("inventory", [])
            existing = next((i for i in inv if i["name"] == armor_piece["name"]), None)
            if existing:
                existing["quantity"] = existing.get("quantity", 1) + 1
            else:
                inv.append({"name": armor_piece["name"], "quantity": 1})
    else:
        # Mob counter-attacks
        m_damage = calculate_mob_damage(mob, player_data)
        player_data["health"] -= m_damage
        turn_log.append(f"⚔️ Враг нанёс <b>{m_damage}</b> урона.")
        if player_data["health"] <= 0:
            player_data["health"] = 0
            session["in_combat"] = False
            session["finished"] = True
            turn_log.append("☠️ <b>Ты погиб!</b>")
    session["turn_messages"] = turn_log
    return session


def process_run(session: dict) -> dict:
    """Handle the player attempting to flee."""
    session["running"] = True
    session["in_combat"] = False
    session["finished"] = True
    session["turn_messages"] = ["🏃‍♂️ Ты сбежал от врага!"]
    return session


# ═══════════════════════════════════════════════════════
# TELEGRAM HANDLERS
# ═══════════════════════════════════════════════════════


async def adventure_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /adventure — show the location selector."""
    locations = get_locations()
    keyboard = []
    # Filter out mobs-only internal entries
    loc_entries = {k: v for k, v in locations.items() if isinstance(v, dict) and "name" in v}
    for loc_id, loc in loc_entries.items():
        duration = loc.get("duration_minutes", 5)
        btn_text = f"{loc['name']} ({duration} мин)"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"adventure.location.{loc_id}")])
    keyboard.append([InlineKeyboardButton("Назад", callback_data="adventure.back")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await send_reply(update, context, message="Выберите локацию для приключения", reply_markup=reply_markup)


async def adventure_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle adventure-related callback queries (location selection / back)."""
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = str(update.effective_user.id)
    if data.startswith("adventure.location:"):
        location_id = data.split(":", 1)[1]
        locations = get_locations()
        location_data = locations.get(location_id)
        if not location_data:
            await query.edit_message_text("Локация не найдена.")
            return
        # Check if already in an active session
        sessions = getattr(context.bot_data, "adventure_sessions", {})
        if user_id in sessions and sessions[user_id].get("in_combat"):
            remaining = int(sessions[user_id]["start_time"] + sessions[user_id]["duration"] - time.time())
            await query.edit_message_text(
                f"У тебя уже есть активное приключение (осталось {remaining // 60} мин)."
            )
            return
        # Create session
        session = create_adventure_session(user_id, location_id, location_data)
        if not session:
            await query.edit_message_text("Не удалось создать приключение.")
            return
        # Store session
        context.bot_data.setdefault("adventure_sessions", {})[user_id] = session
        # Build initial message
        player_name = update.effective_user.username or update.effective_user.first_name
        text = _build_adventure_text(player_name, session["mob"], session["turn_messages"])
        kb = build_combat_keyboard(has_mob=True, in_progress=True)
        await query.edit_message_text(text=text, reply_markup=kb, parse_mode="HTML")
    elif data == "adventure.back":
        await query.edit_message_text("Возвращаемся в меню...")


async def combat_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle combat actions: attack / run."""
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = str(update.effective_user.id)
    sessions = getattr(context.bot_data, "adventure_sessions", {})
    session = sessions.get(user_id)
    if not session or not session.get("in_combat"):
        await query.edit_message_text("Нет активного приключения. Начни новое с помощью /adventure")
        return
    player_name = update.effective_user.username or update.effective_user.first_name
    if data == "combat.attack":
        process_attack(session, user_id)
    elif data == "combat.run":
        process_run(session)
    else:
        return
    # Build updated message
    text = _build_adventure_text(
        player_name,
        session["mob"],
        session["turn_messages"],
        rewards=session.get("rewards"),
        is_running=session.get("running", False),
    )
    kb = build_combat_keyboard(has_mob=session.get("in_combat", False))
    await query.edit_message_text(text=text, reply_markup=kb, parse_mode="HTML")
    # Persist player data after any combat interaction
    all_player_data = get_player_data()
    all_player_data[user_id] = session["player_data"]
    save_player_data(all_player_data)
    # If combat finished (won, died, or ran), update global pool for win
    if session.get("finished") and session.get("rewards"):
        pool = get_global_pool()
        pool.append({
            "mob_name": session["mob"]["name"],
            "location": session["location_id"],
            "time": time.time(),
        })
        save_global_pool(pool)


# ═══════════════════════════════════════════════════════
# BACKGROUND TASK REGISTRATION
# ═══════════════════════════════════════════════════════


async def _check_expired_sessions(context: ContextTypes.DEFAULT_TYPE):
    """Background task that periodically checks for expired adventure sessions."""
    check_expired_adventures(context)


def register_adventure_handlers(app):
    """Register command and callback handlers with the Telegram application."""
    app.add_handler(CommandHandler("adventure", adventure_command))
    app.add_handler(CallbackQueryHandler(adventure_callback, pattern=r"adventure\..*"))
    app.add_handler(CallbackQueryHandler(combat_callback, pattern=r"combat\..*"))
    # Schedule background expiration check every 60 seconds
    app.job_queue.run_repeating(_check_expired_sessions, interval=60, first=30)
