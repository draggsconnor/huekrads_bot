"""
Interactive zone combat during expeditions.

Encounter flow:
  * expedition start plans a random encounter moment (handlers.adventure);
  * the encounter job — or the battle watcher after a restart — starts a
    battle in the chat where the expedition was launched;
  * each round has two 10-second phases: pick an attack zone, then a block
    zone (head / torso / dick);
  * the player's attack hits when it differs from the enemy's hidden block
    zone (2/3); the player blocks the enemy's hidden attack by matching it
    (1/3); a wrong block costs `dmg_per_hit` HP;
  * timeouts auto-assign random zones, so an unattended battle always
    resolves itself;
  * victory at `win_hits` landed hits (the winning hit cancels the enemy's
    counter-attack); defeat when the accumulated damage exceeds the player's
    HP — the gnome crawls home early with no XP and minimal loot.

All state changes happen in sync core functions under the expedition lock;
the async layer only renders messages and manages JobQueue timers.
"""

import logging
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from handlers.adventure import (
    _EXPEDITION_LOCK,
    _apply_level_ups,
    _ensure_player_defaults,
    _get_text,
    _player_key,
    _ru_plural,
    get_player,
    load_global_pool,
    load_locations,
    load_mobs,
    load_player_data,
    parse_expedition_time,
    save_player_data,
    send_expedition_result,
)
from text_resources import get_text, get_text_list, get_text_mapping

logger = logging.getLogger(__name__)

ZONES = ("head", "body", "dick")
PHASE_SECONDS = 10
JOURNAL_LIMIT = 20

TIER_XP = {"common": 10, "uncommon": 15, "rare": 25}
BOSS_XP_BASE = 10
ITEM_DROP_CHANCE = 0.5
VICTORY_HEAL = 20
DEFEAT_LOOT_CHANCE = 0.25
BATTLE_WATCH_INTERVAL = 10


# ---------------------------------------------------------------------------
# Job names and encounter planning
# ---------------------------------------------------------------------------

def encounter_job_name(user_id: int) -> str:
    """JobQueue name for the user's scheduled encounter trigger."""
    return f"encounter_{user_id}"


def battle_job_name(user_id: int) -> str:
    """JobQueue name for the user's battle phase timer."""
    return f"battle_{user_id}"


def plan_encounter(loc: dict[str, Any], duration_seconds: int) -> float | None:
    """Roll whether an encounter happens and pick its moment.

    Returns the delay in seconds from the expedition start (inside the
    window [30s .. end-60s]), or None when the location rolls no encounter
    (uses the location's own `encounter_chance`).
    """
    try:
        chance = float(loc.get("encounter_chance", 0.5) or 0.5)
    except (TypeError, ValueError):
        chance = 0.5
    if random.random() >= chance:
        return None
    earliest = 30.0
    latest = max(earliest + 1.0, float(duration_seconds) - 60.0)
    return random.uniform(earliest, latest)


# ---------------------------------------------------------------------------
# Enemy selection
# ---------------------------------------------------------------------------

def _battle_defaults(mobs_data: dict[str, Any], tier: str) -> dict[str, int]:
    """win_hits / dmg_per_hit defaults for a tier (mobs.json battle_defaults)."""
    defaults = (mobs_data.get("battle_defaults") or {}).get(tier) or {}
    return {
        "win_hits": int(defaults.get("win_hits", 1) or 1),
        "dmg_per_hit": int(defaults.get("dmg_per_hit", 25) or 25),
    }


def _pool_item_name(item_id: str) -> str:
    """Display name for a global-pool item id."""
    items = (load_global_pool().get("items") or {})
    entry = items.get(item_id) or {}
    return entry.get("name") or item_id


def build_battle_enemy(loc_id: str) -> dict[str, Any] | None:
    """Pick the encounter enemy: location boss, specific mob, or rarity roll."""
    locations = load_locations()
    mobs_data = load_mobs()
    loc = locations.get(loc_id) or {}

    # 1) Location boss (first successful roll wins).
    for boss in loc.get("bosses") or []:
        try:
            chance = float(boss.get("encounter_chance", 0) or 0)
        except (TypeError, ValueError):
            continue
        if random.random() < chance:
            cfg = boss.get("battle") or {}
            defaults = _battle_defaults(mobs_data, "boss")
            return {
                "id": boss.get("id"),
                "name": boss.get("display_name") or boss.get("name")
                or _get_text("adventure.unknown_mob"),
                "desc": boss.get("description"),
                "tier": "boss",
                "win_hits": int(cfg.get("win_hits", defaults["win_hits"])),
                "dmg_per_hit": int(cfg.get("dmg_per_hit", defaults["dmg_per_hit"])),
                "base_reward": int(boss.get("base_reward", 0) or 0),
                "loot": boss.get("loot") or [],
                "pool_items": [],
            }

    # 2) Location-specific mobs, weighted by their encounter_chance.
    specific = (mobs_data.get("specific_mobs") or {}).get(loc_id) or []
    if specific:
        weights = []
        for mob in specific:
            try:
                weights.append(float(mob.get("encounter_chance", 1) or 0))
            except (TypeError, ValueError):
                weights.append(0.0)
        total = sum(weights)
        pick = specific[-1]
        if total > 0:
            rand = random.random() * total
            cumulative = 0.0
            for mob, weight in zip(specific, weights):
                cumulative += weight
                if rand <= cumulative:
                    pick = mob
                    break
        mob_id = pick.get("id")
        name = pick.get("name") or _get_text("adventure.unknown_mob")
        desc = pick.get("description")
        if mob_id:
            try:
                name = get_text(f"adventure.{mob_id}_name")
            except KeyError:
                pass
            try:
                desc = get_text(f"adventure.{mob_id}_desc")
            except KeyError:
                pass
        tier = pick.get("tier", "common")
        cfg = pick.get("battle") or {}
        defaults = _battle_defaults(mobs_data, tier)
        pool_items = ((mobs_data.get("loot_table") or {}).get(tier) or {}).get("items") or []
        return {
            "id": mob_id,
            "name": name,
            "desc": desc,
            "tier": tier,
            "win_hits": int(cfg.get("win_hits", defaults["win_hits"])),
            "dmg_per_hit": int(cfg.get("dmg_per_hit", defaults["dmg_per_hit"])),
            "base_reward": 0,
            "loot": pick.get("loot") or [],
            "pool_items": pool_items,
        }

    # 3) Rarity-generated mob.
    weights = mobs_data.get("encounter_weights") or {}
    rarity = "common"
    rand = random.random()
    cumulative = 0.0
    for tier, weight in weights.items():
        try:
            cumulative += float(weight)
        except (TypeError, ValueError):
            continue
        if rand < cumulative:
            rarity = tier
            break
    mob_types = mobs_data.get("mob_types") or {}
    names = ((mob_types.get(rarity) or {}).get("names") or {}).get(loc_id) or [
        f"{rarity.capitalize()} Mob"
    ]
    defaults = _battle_defaults(mobs_data, rarity)
    pool_items = ((mobs_data.get("loot_table") or {}).get(rarity) or {}).get("items") or []
    return {
        "id": None,
        "name": random.choice(names),
        "desc": None,
        "tier": rarity,
        "win_hits": defaults["win_hits"],
        "dmg_per_hit": defaults["dmg_per_hit"],
        "base_reward": 0,
        "loot": [],
        "pool_items": pool_items,
    }


# ---------------------------------------------------------------------------
# Battle state and round logic (pure, no Telegram API inside)
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _phase_deadline(now: datetime | None = None) -> str:
    return ((now or _now()) + timedelta(seconds=PHASE_SECONDS)).isoformat()


def _new_battle(expedition: dict[str, Any]) -> dict[str, Any] | None:
    enemy = build_battle_enemy(expedition.get("location_id"))
    if enemy is None:
        return None
    return {
        "battle_id": uuid.uuid4().hex[:6],
        "location_id": expedition.get("location_id"),
        "chat_id": expedition.get("chat_id"),
        "username": expedition.get("username"),
        "enemy": enemy,
        "round": 0,
        "phase": "attack",
        "enemy_attack": None,
        "enemy_block": None,
        "player_attack": None,
        "player_block": None,
        "auto_attack": False,
        "auto_block": False,
        "phase_deadline": None,
        "hits": 0,
        "dmg_taken": 0,
        "journal": [],
        "message_id": None,
    }


def _start_round(battle: dict[str, Any], now: datetime | None = None) -> None:
    """Begin the next round: hidden enemy zones, fresh choices, attack phase."""
    battle["round"] = int(battle.get("round", 0)) + 1
    battle["enemy_attack"] = random.choice(ZONES)
    battle["enemy_block"] = random.choice(ZONES)
    battle["player_attack"] = None
    battle["player_block"] = None
    battle["auto_attack"] = False
    battle["auto_block"] = False
    battle["phase"] = "attack"
    battle["phase_deadline"] = _phase_deadline(now)


def _roll_loot(loot_table: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Roll a chance-based loot table (boss loot entries)."""
    won = []
    for entry in loot_table:
        chance = entry.get("chance", 0.0)
        qty_min = entry.get("quantity_min", 1)
        qty_max = entry.get("quantity_max", 1)
        if random.random() < chance:
            qty = random.randint(qty_min, qty_max)
            won.append({**entry, "quantity": qty})
    return won


def _resolve_round(battle: dict[str, Any], player: dict[str, Any]) -> str:
    """Settle the current round. Returns 'continue' | 'victory' | 'defeat'.

    The player's attack hits when it differs from the enemy's hidden block
    zone. The winning hit cancels the enemy's counter-attack (ties favour
    the player). A wrong block costs `dmg_per_hit`; defeat when the
    accumulated damage reaches the player's HP.
    """
    enemy = battle["enemy"]
    win_hits = int(enemy.get("win_hits", 1))
    dmg_per_hit = int(enemy.get("dmg_per_hit", 25))

    attack = battle.get("player_attack") or random.choice(ZONES)
    block = battle.get("player_block") or random.choice(ZONES)

    entry = {
        "round": battle["round"],
        "attack": attack,
        "block": block,
        "enemy_attack": battle.get("enemy_attack"),
        "enemy_block": battle.get("enemy_block"),
        "auto": bool(battle.get("auto_attack") or battle.get("auto_block")),
    }

    hit = attack != battle.get("enemy_block")
    entry["hit"] = hit
    if hit:
        battle["hits"] = int(battle.get("hits", 0)) + 1

    if hit and battle["hits"] >= win_hits:
        # Победное попадание — ответный удар в этом раунде не проходит.
        entry["blocked"] = None
        entry["dmg"] = 0
        battle["journal"].append(entry)
        _trim_journal(battle)
        return "victory"

    blocked = block == battle.get("enemy_attack")
    entry["blocked"] = blocked
    dmg = 0 if blocked else dmg_per_hit
    if dmg:
        battle["dmg_taken"] = int(battle.get("dmg_taken", 0)) + dmg
    entry["dmg"] = dmg
    battle["journal"].append(entry)
    _trim_journal(battle)

    hp_left = int(player.get("hp", 200)) - int(battle.get("dmg_taken", 0))
    if hp_left <= 0:
        return "defeat"
    return "continue"


def _trim_journal(battle: dict[str, Any]) -> None:
    if len(battle["journal"]) > JOURNAL_LIMIT:
        del battle["journal"][:-JOURNAL_LIMIT]


def _apply_battle_victory(player: dict[str, Any], battle: dict[str, Any]) -> dict[str, Any]:
    """Victory rewards: XP by tier, loot, healing. Mutates the player dict."""
    enemy = battle["enemy"]
    tier = enemy.get("tier", "common")
    xp = TIER_XP.get(tier)
    if xp is None:  # boss
        xp = BOSS_XP_BASE + int(enemy.get("base_reward", 0) or 0)
    player["xp"] = int(player.get("xp", 0)) + xp

    max_hp = int(player.get("max_hp", 200))
    hp_after = max(1, int(player.get("hp", 200)) - int(battle.get("dmg_taken", 0)))
    healed = max(0, min(VICTORY_HEAL, max_hp - hp_after))
    player["hp"] = min(max_hp, hp_after + healed)

    items: list[dict[str, Any]] = []
    if enemy.get("loot"):
        # Спец-лут моба/босса (например, смоляное яблоко у слуги).
        for item in _roll_loot(enemy["loot"]):
            name = item.get("name") or _pool_item_name(item.get("item_id") or "")
            items.append({"name": name, "quantity": item.get("quantity", 1)})
    if enemy.get("pool_items") and random.random() < ITEM_DROP_CHANCE:
        item_id = random.choice(enemy["pool_items"])
        items.append({"name": _pool_item_name(item_id), "quantity": 1})
    if items:
        player["inventory"] = list(player.get("inventory", [])) + items

    player["total_fights"] = int(player.get("total_fights", 0)) + 1
    player["wins"] = int(player.get("wins", 0)) + 1
    leveled_up = _apply_level_ups(player)

    rewards = {
        "xp": xp,
        "items": items,
        "healed": healed,
        "leveled_up": leveled_up,
        "level": player.get("lvl", 1),
        "max_hp": player.get("max_hp", 200),
    }
    return rewards


def _apply_battle_defeat(player: dict[str, Any], battle: dict[str, Any]) -> dict[str, Any]:
    """Defeat: the gnome crawls home at 1 HP with minimal loot."""
    player["hp"] = 1
    player["total_fights"] = int(player.get("total_fights", 0)) + 1

    items: list[dict[str, Any]] = []
    loc = load_locations().get(battle.get("location_id")) or {}
    resources = loc.get("resources") or []
    if resources and random.random() < DEFEAT_LOOT_CHANCE:
        resource = random.choice(resources)
        items.append({"name": resource.get("name", resource.get("id", "?")), "quantity": 1})
    if items:
        player["inventory"] = list(player.get("inventory", [])) + items
    return {"items": items}


def _settle_battle(player: dict[str, Any], battle: dict[str, Any], outcome: str) -> dict[str, Any]:
    """Clear the battle and apply its outcome. Mutates the player dict."""
    player["active_battle"] = None
    if outcome == "victory":
        rewards = _apply_battle_victory(player, battle)
        expedition = player.get("active_expedition")
        if isinstance(expedition, dict):
            expedition.setdefault("battle_log", []).append({
                "enemy": battle["enemy"].get("name"),
                "result": "victory",
                "rounds": battle["round"],
                "xp": rewards["xp"],
                "item": ", ".join(i["name"] for i in rewards["items"]) or None,
            })
        return rewards
    # Поражение: экспедиция прервана досрочно.
    rewards = _apply_battle_defeat(player, battle)
    player["active_expedition"] = None
    return rewards


# ---------------------------------------------------------------------------
# Sync core: state transitions under the expedition lock
# ---------------------------------------------------------------------------

def try_start_battle(user_id: int) -> dict[str, Any] | None:
    """Start the due encounter battle. Returns the battle state or None.

    Called by the encounter job and by the battle watcher (after a restart).
    One battle per expedition; never starts for an expired expedition.
    """
    now = _now()
    with _EXPEDITION_LOCK:
        data = load_player_data()
        player = data.get(_player_key(user_id))
        if not isinstance(player, dict):
            return None
        _ensure_player_defaults(player)

        expedition = player.get("active_expedition")
        if not isinstance(expedition, dict):
            return None
        if player.get("active_battle"):
            return None
        if expedition.get("battle_log"):
            return None  # у экспедиции уже был бой
        if not expedition.get("encounter_scheduled"):
            return None

        encounter_at = parse_expedition_time(expedition.get("encounter_at"))
        if encounter_at is not None and now < encounter_at:
            return None
        end_time = parse_expedition_time(expedition.get("end_time"))
        if end_time is not None and now >= end_time:
            # Экспедиция уже закончилась — боя не будет, тихий путь.
            expedition["encounter_scheduled"] = False
            save_player_data(data)
            return None

        battle = _new_battle(expedition)
        if battle is None:
            expedition["encounter_scheduled"] = False
            save_player_data(data)
            return None

        _start_round(battle, now)
        player["active_battle"] = battle
        save_player_data(data)
        return battle


def record_battle_choice(user_id: int, battle_id: str, kind: str, zone: str) -> dict[str, Any]:
    """Record the player's zone choice for the current phase.

    Returns an action dict for the async layer:
    {"event": "phase"|"victory"|"defeat", ...} or
    {"event": "stale"|"late"|"invalid"}.
    """
    if kind not in ("attack", "block") or zone not in ZONES:
        return {"event": "invalid"}
    battle_id = (battle_id or "").lower()

    now = _now()
    with _EXPEDITION_LOCK:
        data = load_player_data()
        player = data.get(_player_key(user_id))
        if not isinstance(player, dict):
            return {"event": "stale"}
        battle = player.get("active_battle")
        if not isinstance(battle, dict) or str(battle.get("battle_id", "")).lower() != battle_id:
            return {"event": "stale"}

        expected_phase = "attack" if kind == "attack" else "defense"
        if battle.get("phase") != expected_phase:
            return {"event": "late"}
        choice_key = "player_attack" if kind == "attack" else "player_block"
        if battle.get(choice_key) is not None:
            return {"event": "late"}

        battle[choice_key] = zone
        if kind == "attack":
            battle["phase"] = "defense"
            battle["phase_deadline"] = _phase_deadline(now)
            save_player_data(data)
            return {"event": "phase", "battle": battle}

        # Блок выбран — раунд рассчитывается сразу.
        outcome = _resolve_round(battle, player)
        if outcome == "continue":
            _start_round(battle, now)
            save_player_data(data)
            return {"event": "phase", "battle": battle}
        rewards = _settle_battle(player, battle, outcome)
        save_player_data(data)
        return {"event": outcome, "battle": battle, "rewards": rewards}


def advance_battle(user_id: int, now: datetime | None = None) -> dict[str, Any] | None:
    """Timeout-driven advance: auto-fill a missing choice, move the phase or
    resolve the round. Idempotent: does nothing before the phase deadline.
    """
    now = now or _now()
    with _EXPEDITION_LOCK:
        data = load_player_data()
        player = data.get(_player_key(user_id))
        if not isinstance(player, dict):
            return None
        battle = player.get("active_battle")
        if not isinstance(battle, dict):
            return None

        deadline = parse_expedition_time(battle.get("phase_deadline"))
        if deadline is not None and now < deadline:
            return None

        phase = battle.get("phase")
        if phase == "attack" and battle.get("player_attack") is None:
            battle["player_attack"] = random.choice(ZONES)
            battle["auto_attack"] = True
        if phase == "defense" and battle.get("player_block") is None:
            battle["player_block"] = random.choice(ZONES)
            battle["auto_block"] = True

        if phase == "attack":
            battle["phase"] = "defense"
            battle["phase_deadline"] = _phase_deadline(now)
            save_player_data(data)
            return {"event": "phase", "battle": battle}

        outcome = _resolve_round(battle, player)
        if outcome == "continue":
            _start_round(battle, now)
            save_player_data(data)
            return {"event": "phase", "battle": battle}
        rewards = _settle_battle(player, battle, outcome)
        save_player_data(data)
        return {"event": outcome, "battle": battle, "rewards": rewards}


def abort_battle(user_id: int) -> bool:
    """Drop an active battle without rewards (expedition cancelled)."""
    with _EXPEDITION_LOCK:
        data = load_player_data()
        player = data.get(_player_key(user_id))
        if not isinstance(player, dict) or not isinstance(player.get("active_battle"), dict):
            return False
        player["active_battle"] = None
        save_player_data(data)
        return True


def set_battle_message_id(user_id: int, message_id: int) -> None:
    """Remember the chat message that renders the battle."""
    with _EXPEDITION_LOCK:
        data = load_player_data()
        player = data.get(_player_key(user_id))
        if isinstance(player, dict) and isinstance(player.get("active_battle"), dict):
            player["active_battle"]["message_id"] = message_id
            save_player_data(data)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _battle_keyboard(battle: dict[str, Any]) -> InlineKeyboardMarkup:
    phase = battle.get("phase")
    if phase == "defense":
        labels = get_text_mapping("battle.block_buttons")
        kind = "blk"
    else:
        labels = get_text_mapping("battle.attack_buttons")
        kind = "atk"
    bid = battle.get("battle_id", "")
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(labels[zone], callback_data=f"advb_{bid}_{kind}_{zone}")
                for zone in ZONES
            ]
        ]
    )


def _battle_username(battle: dict[str, Any]) -> str:
    return battle.get("username") or _get_text("common.user.default_title")


def _battle_status(battle: dict[str, Any]) -> str:
    enemy = battle.get("enemy") or {}
    return get_text(
        "battle.status",
        hits=battle.get("hits", 0),
        win_hits=enemy.get("win_hits", 1),
        dmg=battle.get("dmg_taken", 0),
    )


def _battle_phase_line(battle: dict[str, Any]) -> str:
    if battle.get("phase") == "defense":
        return get_text("battle.phase_defense", round=battle.get("round", 1))
    return get_text("battle.phase_attack", round=battle.get("round", 1))


def _round_art(battle: dict[str, Any], entry: dict[str, Any] | None = None, final: bool = False) -> str | None:
    """Художественное описание раунда из каталогов фраз (для фаз и отчётов).

    Побединый хит гасит ответку — арт победного раунда состоит из одной строки.
    """
    entry = entry or (battle.get("journal") or [None])[-1]
    if not entry:
        return None
    enemy_name = (battle.get("enemy") or {}).get("name", "?")
    player = _battle_username(battle)
    attack_names = get_text_mapping("battle.zone_attack_names")
    block_names = get_text_mapping("battle.zone_block_names")
    attack = attack_names.get(entry.get("attack"), "?")
    counter = attack_names.get(entry.get("enemy_attack"), "?")
    block = block_names.get(entry.get("block"), "?")

    lines = []
    if entry.get("hit"):
        catalog = "battle.final_blow_phrases" if final else "battle.attack_hit_phrases"
        lines.append(random.choice(get_text_list(catalog)).format(
            player=player, enemy=enemy_name, attack=attack))
    else:
        lines.append(random.choice(get_text_list("battle.attack_miss_phrases")).format(
            player=player, enemy=enemy_name, attack=attack))

    if entry.get("blocked") is None:
        pass  # победный раунд: ответный удар не проходит
    elif entry.get("blocked"):
        lines.append(random.choice(get_text_list("battle.counter_blocked_phrases")).format(
            player=player, enemy=enemy_name, counter=counter, block=block))
    else:
        lines.append(random.choice(get_text_list("battle.counter_hit_phrases")).format(
            player=player, enemy=enemy_name, counter=counter, dmg=entry.get("dmg", 0)))
    return "\n".join(lines)


def _render_stats_block(battle: dict[str, Any]) -> str:
    enemy = battle.get("enemy") or {}
    rounds = int(battle.get("round", 0) or 0)
    tier_labels = get_text_mapping("battle.tier_labels")
    tier = enemy.get("tier", "common")
    # .strip(): YAML-блок '| сохраняет перевод строки в конце — для раскладки
    # отчёта пустые строки между секциями добавляет код.
    return get_text(
        "battle.stats_block",
        enemy=enemy.get("name", "?"),
        tier=tier_labels.get(tier, tier),
        hits=battle.get("hits", 0),
        win_hits=enemy.get("win_hits", 1),
        dmg=battle.get("dmg_taken", 0),
        rounds=rounds,
        rounds_word=_ru_plural(rounds, "раунд", "раунда", "раундов"),
    ).strip()


def _lore_line() -> str:
    return get_text("battle.lore_line", lore=random.choice(get_text_list("battle.lore_quotes")))


def _battle_message_text(battle: dict[str, Any]) -> str:
    # .rstrip(): YAML-блок message оставляет хвостовой \n после {phase}
    text = get_text(
        "battle.message",
        username=_battle_username(battle),
        enemy=(battle.get("enemy") or {}).get("name", "?"),
        status=_battle_status(battle),
        phase=_battle_phase_line(battle),
    ).rstrip()
    # Арт только что закончившегося раунда — как мгновенная реакция.
    art = _round_art(battle)
    if art:
        text += "\n\n" + art
    return text


def _battle_intro_text(battle: dict[str, Any]) -> str:
    enemy = battle.get("enemy") or {}
    username = _battle_username(battle)
    lines = [get_text("battle.intro_title", username=username)]
    if enemy.get("desc"):
        lines.append(get_text("battle.intro_enemy", enemy=enemy.get("name"), desc=enemy["desc"]))
    else:
        lines.append(get_text("battle.intro_enemy_plain", enemy=enemy.get("name")))
    lines.append(
        get_text("battle.intro_params", win_hits=enemy.get("win_hits", 1), dmg=enemy.get("dmg_per_hit", 25))
    )
    lines.append("")
    lines.append(_battle_status(battle))
    lines.append(_battle_phase_line(battle))
    return "\n".join(lines)


def _journal_lines(battle: dict[str, Any]) -> list[str]:
    attack_names = get_text_mapping("battle.zone_attack_names")
    block_names = get_text_mapping("battle.zone_block_names")
    lines = []
    for entry in battle.get("journal") or []:
        attack = attack_names.get(entry.get("attack"), "?")
        block = block_names.get(entry.get("block"), "?")
        if entry.get("hit") and entry.get("blocked"):
            key = "battle.round_hit_blocked"
        elif entry.get("hit"):
            key = "battle.round_win" if entry.get("blocked") is None else "battle.round_hit_damaged"
        elif entry.get("blocked"):
            key = "battle.round_miss_blocked"
        else:
            key = "battle.round_miss_damaged"
        line = get_text(
            key,
            round=entry.get("round"),
            attack=attack,
            block=block,
            dmg=entry.get("dmg", 0),
        )
        if entry.get("auto"):
            line = "⌛ " + line
        lines.append(line)
    return lines


def _victory_report(battle: dict[str, Any], rewards: dict[str, Any]) -> str:
    lines = [get_text("battle.victory_title", username=_battle_username(battle)), ""]
    journal = battle.get("journal") or []
    if journal:
        lines.append(_round_art(battle, final=True, entry=journal[-1]))
        lines.append("")
    lines.append(_render_stats_block(battle))
    lines.append("")
    if rewards.get("healed"):
        lines.append(get_text("battle.victory_heal", hp=rewards["healed"]))
    lines.append(get_text("battle.reward_xp", xp=rewards.get("xp", 0)))
    for item in rewards.get("items") or []:
        lines.append(get_text("battle.reward_item", item=item.get("name"), count=item.get("quantity", 1)))
    if rewards.get("leveled_up"):
        lines.append("")
        lines.append(get_text("adventure.level_up", level=rewards.get("level"), max_hp=rewards.get("max_hp")))
    lines.append("")
    lines.append(get_text("battle.journal_header"))
    lines.extend(_journal_lines(battle))
    lines.append("")
    lines.append(_lore_line())
    return "\n".join(lines)


def _defeat_report(battle: dict[str, Any], rewards: dict[str, Any]) -> str:
    lines = [get_text("battle.defeat_title", username=_battle_username(battle)), ""]
    journal = battle.get("journal") or []
    if journal:
        lines.append(_round_art(battle, entry=journal[-1]))
        lines.append("")
    lines.append(_render_stats_block(battle))
    lines.append("")
    for item in rewards.get("items") or []:
        lines.append(get_text("battle.defeat_loot", item=item.get("name")))
    lines.append(get_text("battle.defeat_end"))
    lines.append("")
    lines.append(get_text("battle.journal_header"))
    lines.extend(_journal_lines(battle))
    lines.append("")
    lines.append(_lore_line())
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Async layer: Telegram delivery and JobQueue wiring
# ---------------------------------------------------------------------------

def _schedule_phase_timer(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """(Re)schedule the phase timeout job for the user's battle."""
    if not context.job_queue:
        return
    for job in context.job_queue.get_jobs_by_name(battle_job_name(user_id)):
        job.schedule_removal()
    context.job_queue.run_once(
        battle_phase_timeout,
        PHASE_SECONDS + 1,
        name=battle_job_name(user_id),
        data={"user_id": user_id},
    )


async def _send_battle_message(user_id: int, battle: dict[str, Any], context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a fresh battle message (start of the battle)."""
    chat_id = battle.get("chat_id") or user_id
    try:
        message = await context.bot.send_message(
            chat_id=chat_id,
            text=_battle_intro_text(battle),
            parse_mode="HTML",
            reply_markup=_battle_keyboard(battle),
        )
        battle["message_id"] = message.message_id
        set_battle_message_id(user_id, message.message_id)
    except Exception as e:
        logger.error("Failed to send battle message for %s: %s", user_id, e)


async def _edit_battle_message(user_id: int, battle: dict[str, Any], context: ContextTypes.DEFAULT_TYPE) -> None:
    """Edit the battle message into the current phase; re-send if deleted."""
    chat_id = battle.get("chat_id") or user_id
    message_id = battle.get("message_id")
    if not message_id:
        await _send_battle_message(user_id, battle, context)
        return
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=_battle_message_text(battle),
            parse_mode="HTML",
            reply_markup=_battle_keyboard(battle),
        )
    except Exception:
        # Сообщение могли удалить — шлём новое и запоминаем его.
        try:
            message = await context.bot.send_message(
                chat_id=chat_id,
                text=_battle_message_text(battle),
                parse_mode="HTML",
                reply_markup=_battle_keyboard(battle),
            )
            battle["message_id"] = message.message_id
            set_battle_message_id(user_id, message.message_id)
        except Exception as e:
            logger.error("Failed to update battle message for %s: %s", user_id, e)


async def _send_battle_report(
    user_id: int,
    battle: dict[str, Any],
    rewards: dict[str, Any],
    event: str,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Replace the battle message with the final report (no buttons)."""
    chat_id = battle.get("chat_id") or user_id
    text = _victory_report(battle, rewards) if event == "victory" else _defeat_report(battle, rewards)
    message_id = battle.get("message_id")
    if message_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                parse_mode="HTML",
            )
            return
        except Exception:
            pass  # сообщение удалено — отправим новое ниже
    try:
        await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
    except Exception as e:
        logger.error("Failed to send battle report for %s: %s", user_id, e)


async def _act_on_battle_action(user_id: int, action: dict[str, Any] | None, context: ContextTypes.DEFAULT_TYPE) -> None:
    """React to a sync-core action: update the message, timers, follow-ups."""
    if not action:
        return
    event = action.get("event")
    battle = action.get("battle") or {}

    if event == "phase":
        await _edit_battle_message(user_id, battle, context)
        _schedule_phase_timer(user_id, context)
        return

    if event in ("victory", "defeat"):
        await _send_battle_report(user_id, battle, action.get("rewards") or {}, event, context)
        if context.job_queue:
            for job in context.job_queue.get_jobs_by_name(battle_job_name(user_id)):
                job.schedule_removal()
        if event == "victory":
            # Экспедиция могла истечь, пока шёл бой, — завершим её сразу.
            player = get_player(user_id)
            expedition = player.get("active_expedition") if player else None
            if isinstance(expedition, dict):
                end_time = parse_expedition_time(expedition.get("end_time"))
                if end_time is not None and _now() >= end_time:
                    await send_expedition_result(user_id, None, None, context)
        return


async def trigger_encounter_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue callback: start the due encounter battle."""
    job_data = context.job.data if context.job else {}
    user_id = job_data.get("user_id")
    if not user_id:
        return
    await start_encounter_battle(int(user_id), context)


async def start_encounter_battle(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start the battle (if due) and deliver its first message."""
    battle = try_start_battle(user_id)
    if battle is None:
        return
    logger.info("Battle started for user %s: %s", user_id, battle["enemy"]["name"])
    await _send_battle_message(user_id, battle, context)
    _schedule_phase_timer(user_id, context)


async def battle_phase_timeout(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue callback: advance a battle phase whose deadline passed."""
    job_data = context.job.data if context.job else {}
    user_id = job_data.get("user_id")
    if not user_id:
        return
    action = advance_battle(int(user_id))
    await _act_on_battle_action(int(user_id), action, context)


async def battle_watcher_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Safety net (runs every ~10s): re-drive stalled battle phases and
    start encounter battles missed after a bot restart."""
    now = _now()
    try:
        player_data = load_player_data()
        for user_id, player in player_data.items():
            if not user_id.isdigit() or not isinstance(player, dict):
                continue

            battle = player.get("active_battle")
            if isinstance(battle, dict):
                deadline = parse_expedition_time(battle.get("phase_deadline"))
                if deadline is None or now >= deadline:
                    action = advance_battle(int(user_id))
                    await _act_on_battle_action(int(user_id), action, context)
                continue

            expedition = player.get("active_expedition")
            if not isinstance(expedition, dict):
                continue
            if not expedition.get("encounter_scheduled") or expedition.get("battle_log"):
                continue
            encounter_at = parse_expedition_time(expedition.get("encounter_at"))
            if encounter_at is not None and now >= encounter_at:
                await start_encounter_battle(int(user_id), context)
    except Exception as e:
        logger.error("Error in battle_watcher_job: %s", e)


async def battle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle advb_{battle_id}_{atk|blk}_{zone} inline callbacks."""
    query = update.callback_query

    data = query.data or ""
    parts = data.split("_")
    user = update.effective_user
    if len(parts) != 4 or user is None:
        await query.answer()
        return
    _, battle_id, kind_raw, zone = parts
    kind = {"atk": "attack", "blk": "block"}.get(kind_raw)
    if kind is None or zone not in ZONES:
        await query.answer()
        return

    action = record_battle_choice(user.id, battle_id, kind, zone)
    event = action.get("event")
    if event == "stale":
        await query.answer(get_text("battle.stale"))
    elif event in ("late", "invalid"):
        await query.answer(get_text("battle.late"))
    else:
        await query.answer()
        await _act_on_battle_action(user.id, action, context)
