"""Модели данных игрока и результатов экспедиций."""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Optional

from .config import INITIAL_STATS
from .storage import save_player


@dataclass
class ExpeditionResult:
    """Результат симуляции экспедиции. Сохраняется пока ждём таймер."""

    location_id: int
    is_boss: bool
    won: bool
    loot: list  # [{"item_key": str, "name": str, "count": int, ...}, ...]
    xp_gained: int
    messages: list[str]  # то, что покажем игроку по окончании


@dataclass
class Player:
    """Модель игрока"""

    tg_id: int

    # Основные характеристики
    name: str = ""
    hp: int = INITIAL_STATS["hp"]
    max_hp: int = INITIAL_STATS["max_hp"]
    attack: int = INITIAL_STATS["attack"]
    defense: int = INITIAL_STATS["defense"]

    # Прогресс
    level: int = INITIAL_STATS["level"]
    xp: int = INITIAL_STATS["xp"]
    xp_to_next: int = INITIAL_STATS["xp_to_next"]
    gold: int = INITIAL_STATS["gold"]

    # Статистика
    boss_kills: int = 0
    completed_expeditions: int = 0

    # Инвентарь: {"item_key": count, ...}
    inventory: dict = field(default_factory=dict)

    # Системные поля
    expedition: Optional[dict] = None  # {"location_id": int, "end_time": float}
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    # --------------------------------------------------------------
    # Сериализация
    # --------------------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Player":
        valid_fields = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid_fields)

    # --------------------------------------------------------------
    # Жизненный цикл
    # --------------------------------------------------------------
    def is_dead(self) -> bool:
        return self.hp <= 0

    def heal(self, amount: int) -> None:
        self.hp = min(self.hp + amount, self.max_hp)

    def take_damage(self, amount: int) -> None:
        actual = max(amount - self.defense, 1)
        self.hp = max(self.hp - actual, 0)

    # --------------------------------------------------------------
    # XP / уровни
    # --------------------------------------------------------------
    def add_xp(self, amount: int) -> list[str]:
        """Добавить XP, обработать повышение уровня. Возвращает список сообщений."""
        messages: list[str] = []
        self.xp += amount
        while self.xp >= self.xp_to_next:
            self.xp -= self.xp_to_next
            self.level_up()
            messages.append(
                f"🎉 Уровень повышен до {self.level}!\n"
                f"   ❤️ HP: +10 | ⚔️ Атака: +2 | 🛡 Защита: +1"
            )
        return messages

    def level_up(self) -> None:
        self.level += 1
        self.xp_to_next = int(self.xp_to_next * 1.3)
        self.max_hp += 10
        self.attack += 2
        self.defense += 1
        self.hp = self.max_hp

    # --------------------------------------------------------------
    # Экспедиции
    # --------------------------------------------------------------
    def expedition_active(self) -> bool:
        if self.expedition is None:
            return False
        return time.time() < self.expedition["end_time"]

    def expedition_time_left(self) -> Optional[int]:
        """Оставшиеся секунды до конца экспедиции, или None."""
        if not self.expedition_active():
            return None
        return max(0, int(self.expedition["end_time"] - time.time()))

    # --------------------------------------------------------------
    # Инвентарь
    # --------------------------------------------------------------
    def add_loot(self, item: dict) -> None:
        """Атомарно добавить предмет с учётом count."""
        key = item["item_key"]
        qty = item.get("count", 1)
        self.inventory[key] = self.inventory.get(key, 0) + qty

    def formatted_inventory(self) -> str:
        """Красивый вывод инвентаря, или сообщение о пустоте."""
        if not self.inventory:
            return "📦 Инвентарь пуст."
        lines = ["📦 Инвентарь:"]
        for key, qty in sorted(self.inventory.items()):
            lines.append(f"   {key} x{qty}")
        return "\n".join(lines)

    # --------------------------------------------------------------
    # Персистентность
    # --------------------------------------------------------------
    def save(self) -> None:
        self.updated_at = time.time()
        save_player(self)