# ==========================================
# COMBAT — auto-battle simulation + loot
# ==========================================

import random
from typing import Dict, List, Tuple

from config import ITEMS, CREATURE_PETS
from models import ExpeditionResult, Player


def _calc_dmg(attacker_stats: dict, defender_hp: int, is_player: bool, level: int) -> int:
    """Base damage with Str scaling, some randomness."""
    base = 5 + attacker_stats.get("str", 5) * 2
    variance = random.uniform(0.8, 1.2)
    dmg = int(base * variance)
    # crit
    crit_chance = attacker_stats.get("lck", 5) * 1.5  # %
    if random.uniform(0, 100) < crit_chance:
        dmg = int(dmg * 1.5)
    return max(1, dmg)


def _player_pet_dmg(player: Player) -> int:
    """If player has a pet companion equipped, add its damage."""
    # pets list contains dicts: {"name": "Hue", "type": "creature", ...}
    # for now simple fixed bonus per pet
    dmg = 0
    for _ in player.pets:
        dmg += random.randint(2, 6)
    return dmg


def run_auto_battle(player: Player, mob_stats: dict, is_boss: bool = False) -> ExpeditionResult:
    """
    Real-time auto-battle.
    Returns ExpeditionResult with won/loot/xp/messages.
    """
    # Player stats
    max_hp = player.max_hp
    hp = max_hp
    str_ = player.stats.get("str", 5)
    dex = player.stats.get("dex", 5)
    lck = player.stats.get("lck", 5)

    # Enemy stats
    enemy_base_hp = mob_stats.get("base_hp", 30)
    enemy_hp = enemy_base_hp
    enemy_dmg = mob_stats.get("damage", 5)
    enemy_level = mob_stats.get("level", 1)
    enemy_name = mob_stats.get("name", "Монстр")

    # Dodge chance from Dex
    dodge_chance = min(30, dex * 1.5)  # cap at 30%
    messages: List[str] = []

    if is_boss:
        messages.append(f"👹 БОСС: {enemy_name} появился! HP: {enemy_hp}")
    else:
        messages.append(f"⚔️ Бой с {enemy_name} (ур. {enemy_level}) начался! Твой HP: {hp}")

    round_num = 0
    while hp > 0 and enemy_hp > 0:
        round_num += 1
        # Player attacks
        dmg = _calc_dmg(player.stats, enemy_hp, is_player=True, level=player.level)
        dmg += _player_pet_dmg(player)
        enemy_hp -= dmg
        messages.append(f"  Ты наносишь {dmg} урона. {enemy_name} HP: {max(0, enemy_hp)}")

        if enemy_hp <= 0:
            break

        # Enemy attacks
        # dodge check
        if random.uniform(0, 100) < dodge_chance:
            messages.append(f"  🌪 Уклонился от атаки!")
            continue

        # enemy damage variance
        e_dmg = int(enemy_dmg * random.uniform(0.9, 1.1))
        hp -= e_dmg
        messages.append(f"  {enemy_name} бьёт на {e_dmg}! Твой HP: {max(0, hp)}")

    won = hp > 0
    result = ExpeditionResult(
        location_id=mob_stats.get("location_id", 0),
        is_boss=is_boss,
        won=won,
        loot=[],
        xp_gained=0,
        messages=messages,
    )

    if won:
        # XP
        base_xp = mob_stats.get("reward_xp", 10)
        if is_boss:
            base_xp *= 3
        result.xp_gained = base_xp

        # Loot
        loot_table: List[str] = mob_stats.get("loot_table", [])
        result.loot = _roll_loot(loot_table, lck, is_boss)

        if is_boss:
            messages.append(f"🏆 Победа над боссом! +{base_xp} XP")
        else:
            messages.append(f"✅ Победа! +{base_xp} XP")
        if result.loot:
            msgs = ", ".join(f"{i['name']} x{i['quantity']}" for i in result.loot)
            messages.append(f"💎 Лут: {msgs}")
    else:
        # defeat: 50% xp
        result.xp_gained = mob_stats.get("reward_xp", 10) // 2
        messages.append(f"💀 Поражение... +{result.xp_gained} XP (насмешка Харона)")

    return result


def _roll_loot(loot_table: List[str], luck: int, is_boss: bool) -> List[dict]:
    rolls = 2 if is_boss else 1
    results: List[Dict[str, int]] = []
    for _ in range(rolls):
        if not loot_table:
            continue
        # luck increases chance to get something
        if random.uniform(0, 100) > (40 - luck * 2):  # base 40% chance, +2% per luck point
            continue
        item_id = random.choice(loot_table)
        item = ITEMS.get(item_id)
        if item is None:
            continue
        qty = random.randint(item.get("min_drop", 1), item.get("max_drop", 1))
        results.append({"id": item_id, "name": item["name"], "quantity": qty})
    return results


def roll_pet(location_id: int) -> Tuple[bool, dict]:
    """Roll for a creature pet drop. Returns (success, pet_data)."""
    # chance: 0.5% per expedition
    if random.random() > 0.005:
        return False, {}
    candidates = [p for p in CREATURE_PETS if p.get("location_id") == location_id]
    if not candidates:
        return False, {}
    chosen = random.choice(candidates)
    pet = {
        "name": chosen["name"],
        "type": "creature",
        "location_id": location_id,
        "companion": False,  # user must activate
    }
    return True, pet