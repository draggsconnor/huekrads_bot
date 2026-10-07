from __future__ import annotations

import random
from typing import List

from .config import BASE_XP_PER_EXPEDITION, BOSS_XP_BONUS, DROP_ROLLS, LOOT_CHANCE_MULTIPLIER, LOCATIONS
from .models import ExpeditionResult, Player


def resolve_expedition(player: Player, location_id: int) -> ExpeditionResult:
    """Полностью симулирует экспедицию. Вызывается СРАЗУ при нажатии кнопки 'Отправиться'.
    Результат сохраняется и показывается по таймеру."""
    location = LOCATIONS[location_id]
    messages: List[str] = []

    # --- Босс? ---
    is_boss = random.randint(1, 100) <= location["boss_chance"]

    if is_boss:
        messages.append(f"⚠️ Внезапно появляется {location['boss_name']}!")
        # Шанс победы over боссом — скалируется с уровнем
        # Базово 30%, +5% за каждый уровень, макс 80%
        win_chance = min(30 + (player.level * 5), 80)
        won = random.randint(1, 100) <= win_chance

        if won:
            messages.append(f"🏆 Ты победил {location['boss_name']}!")
            xp_gained = BOSS_XP_BONUS + (player.level * 10)
            messages.append(f"⭐ +{xp_gained} XP")
            loot = _roll_loot(location, rolls=DROP_ROLLS + 2, is_boss=True)  # Больше лута за босса
            if loot:
                messages.append("🎁 Трофеи:")
                for item in loot:
                    qty = f" x{item['count']}" if item.get("count", 1) > 1 else ""
                    messages.append(f"   {item['name']}{qty}")
            player.boss_kills += 1
        else:
            messages.append(f"💀 {location['boss_name']} разгромил тебя.")
            xp_gained = BASE_XP_PER_EXPEDITION // 2
            messages.append(f"⭐ +{xp_gained} XP (выжил, но едва)")
            loot = []
    else:
        # Обычная экспедиция
        won = True
        xp_gained = BASE_XP_PER_EXPEDITION + random.randint(0, 10)
        messages.append(f"✅ Экспедиция в {location['name']} завершена!")
        messages.append(f"⭐ +{xp_gained} XP")
        loot = _roll_loot(location, rolls=DROP_ROLLS, is_boss=False)
        if loot:
            messages.append("🎁 Найдено:")
            for item in loot:
                qty = f" x{item['count']}" if item.get("count", 1) > 1 else ""
                messages.append(f"   {item['name']}{qty}")

    # Начисляем XP
    player.add_xp(xp_gained)

    # Добавляем лут в инвентарь (атомарно)
    for item in loot:
        _add_loot_safe(player, item)

    player.completed_expeditions += 1
    player.save()

    return ExpeditionResult(
        location_id=location_id,
        is_boss=is_boss,
        won=won,
        loot=loot,
        xp_gained=xp_gained,
        messages=messages,
    )


def _roll_loot(location: dict, rolls: int, is_boss: bool) -> List[dict]:
    """Роллит лут из таблицы дропов локации."""
    drops = location.get("drops", [])
    if not drops:
        return []

    loot: List[dict] = []
    for _ in range(rolls):
        # Выбираем один предмет по шансу
        roll = random.randint(1, 100)
        cumulative = 0
        for drop in drops:
            cumulative += int(drop["chance"] * LOOT_CHANCE_MULTIPLIER)
            if roll <= cumulative:
                loot.append(drop)
                break
        else:
            # Ничего не выпало
            pass

    # Уникализируем по имени, но считаем количество
    merged: dict = {}
    for item in loot:
        key = item["item_key"]
        if key not in merged:
            merged[key] = {**item, "count": 1}
        else:
            merged[key]["count"] += 1

    return list(merged.values())