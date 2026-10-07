# ==========================================
# SCHEDULER — expedition timers (async callbacks)
# ==========================================

import asyncio
from datetime import datetime, timedelta
from typing import Callable, Coroutine, Dict

from config import BOSSES, EXPEDITION_COOLDOWN_SECONDS, HOURS_PER_EXPEDITION
from models import ExpeditionResult, Player


# In-memory timer registry: tg_id -> asyncio.Task
_timers: Dict[int, asyncio.Task] = {}


# Signature for the callback that handles expedition completion:
# async def on_expedition_complete(player: Player, result: ExpeditionResult) -> None
CallbackType = Callable[[Player, ExpeditionResult], Coroutine]


def start_expedition_timer(
    player: Player,
    location_id: int,
    callback: CallbackType,
) -> None:
    """Start or restart an expedition timer for a player."""
    # Cancel existing timer if any
    cancel_timer(player.tg_id)

    duration_hours = HOURS_PER_EXPEDITION.get(location_id, 1.5)
    duration_seconds = int(duration_hours * EXPEDITION_COOLDOWN_SECONDS)
    end_time = datetime.now() + timedelta(seconds=duration_seconds)

    # Build end time info on player
    player.active_expedition = {
        "location_id": location_id,
        "end_time_iso": end_time.isoformat(),
        "boss": False,
    }

    task = asyncio.create_task(
        _wait_then_callback(player, location_id, duration_seconds, callback)
    )
    _timers[player.tg_id] = task


async def _wait_then_callback(
    player: Player,
    location_id: int,
    duration_seconds: int,
    callback: CallbackType,
) -> None:
    """Sleep then simulate combat and call handler."""
    from combat import run_auto_battle, roll_pet  # avoid circular at module init

    await asyncio.sleep(duration_seconds)

    # Determine boss or mob
    boss_data = BOSSES.get(location_id)
    is_boss = False
    if boss_data:
        # 30% boss chance
        import random

        if random.random() < 0.30:
            is_boss = True
            mob = boss_data
        else:
            # Pick random mob for this location from config
            from config import MOBS

            mobs_here = [m for m in MOBS if m.get("location_id") == location_id]
            mob = mobs_here[0] if mobs_here else {"name": "Неизвестный", "base_hp": 10}
    else:
        from config import MOBS

        mobs_here = [m for m in MOBS if m.get("location_id") == location_id]
        mob = mobs_here[0] if mobs_here else {"name": "Неизвестный", "base_hp": 10}

    result = run_auto_battle(player, mob, is_boss=is_boss)

    # Pet drop roll (only on mob kill, higher chance if boss)
    if result.won:
        pet_success, pet_data = roll_pet(location_id)
        if pet_success and pet_data:
            player.pets.append(pet_data)
            result.messages.append(
                f"✨ Ты приручил существо: {pet_data['name']}!"
            )

    # Increment completed
    player.completed_expeditions += 1
    if is_boss and result.won:
        player.boss_kills += 1

    # Clear active expedition
    player.active_expedition = None

    # Callback (e.g. send message to user, award items)
    await callback(player, result)

    # Cleanup timer
    _timers.pop(player.tg_id, None)


def cancel_timer(tg_id: int) -> None:
    """Cancel active expedition timer for a user. No-op if none."""
    task = _timers.pop(tg_id, None)
    if task and not task.done():
        task.cancel()


def is_on_expedition(player: Player) -> bool:
    """Check if player has an active expedition (based on timer + active_expedition data)."""
    if player.active_expedition is None:
        return False
    # Also check if timer exists
    task = _timers.get(player.tg_id)
    if task is None:
        return False
    return not task.done()


def seconds_remaining(player: Player) -> int:
    """Return seconds left for current expedition, or 0 if none/done."""
    if player.active_expedition is None:
        return 0
    end_iso = player.active_expedition.get("end_time_iso")
    if not end_iso:
        return 0
    end = datetime.fromisoformat(end_iso)
    diff = (end - datetime.now()).total_seconds()
    return max(0, int(diff))


def restore_timers_on_startup(
    all_players: list[Player],
    callback: CallbackType,
) -> None:
    """After bot restart, recreate asyncio tasks for any players with active_expedition."""
    now = datetime.now()
    for p in all_players:
        if not p.active_expedition:
            continue
        end_iso = p.active_expedition.get("end_time_iso")
        if not end_iso:
            # Corrupt data, clear it
            p.active_expedition = None
            continue
        end = datetime.fromisoformat(end_iso)
        remaining = int((end - now).total_seconds())
        if remaining <= 0:
            # Timer already expired while bot was down: fire immediately
            task = asyncio.create_task(
                _expedition_fire_now(p, p.active_expedition.get("location_id", 0), callback)
            )
        else:
            task = asyncio.create_task(
                _wait_then_callback(p, p.active_expedition.get("location_id", 0), remaining, callback)
            )
        _timers[p.tg_id] = task


async def _expedition_fire_now(
    player: Player,
    location_id: int,
    callback: CallbackType,
) -> None:
    from combat import run_auto_battle, roll_pet

    from config import BOSSES, MOBS

    boss_data = BOSSES.get(location_id)
    is_boss = False
    if boss_data:
        import random

        if random.random() < 0.30:
            is_boss = True
            mob = boss_data
        else:
            mobs_here = [m for m in MOBS if m.get("location_id") == location_id]
            mob = mobs_here[0] if mobs_here else {"name": "Неизвестный", "base_hp": 10}
    else:
        mobs_here = [m for m in MOBS if m.get("location_id") == location_id]
        mob = mobs_here[0] if mobs_here else {"name": "Неизвестный", "base_hp": 10}

    result = run_auto_battle(player, mob, is_boss=is_boss)

    if result.won:
        pet_success, pet_data = roll_pet(location_id)
        if pet_success and pet_data:
            player.pets.append(pet_data)
            result.messages.append(
                f"✨ Ты приручил существо: {pet_data['name']}!"
            )

    player.completed_expeditions += 1
    if is_boss and result.won:
        player.boss_kills += 1

    player.active_expedition = None
    await callback(player, result)
    _timers.pop(player.tg_id, None)