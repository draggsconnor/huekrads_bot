# ==========================================
# MODELS — Domain models for the game
# ==========================================

from __future__ import annotations
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Player:
    tg_id: int
    tg_username: Optional[str] = None
    display_name: Optional[str] = None
    level: int = 1
    xp: int = 0
    stats: Dict[str, int] = field(default_factory=dict)
    equipment: Dict[str, Optional[str]] = field(default_factory=dict)
    inventory: Dict[str, int] = field(default_factory=dict)
    pets: List[Dict] = field(default_factory=list)
    active_expedition: Optional[Dict] = None
    completed_expeditions: int = 0
    boss_kills: int = 0

    # defaults
    DEFAULT_STATS = {
        "str": 5,   # strength -> base damage
        "vit": 5,   # vitality -> HP
        "dex": 5,   # dexterity -> dodge chance
        "lck": 5,   # luck -> crit chance / drop chance
    }

    def __post_init__(self):
        for key, value in self.DEFAULT_STATS.items():
            self.stats.setdefault(key, value)
        # equipment slots
        for slot in ("weapon", "armor", "helmet", "boots", "accessory"):
            self.equipment.setdefault(slot, None)

    @property
    def max_hp(self) -> int:
        return 50 + self.stats["vit"] * 10 + (self.level - 1) * 5

    @property
    def xp_to_next(self) -> int:
        return 100 + (self.level - 1) * 50

    def add_xp(self, amount: int) -> List[str]:
        """Returns list of level-up messages."""
        messages: List[str] = []
        self.xp += amount
        while self.xp >= self.xp_to_next:
            self.xp -= self.xp_to_next
            self.level += 1
            self.stat_points += 1
            messages.append(f"🎉 Уровень повышен! Теперь ты {self.level} уровня.")
        return messages

    def to_dict(self) -> dict:
        return {
            "tg_id": self.tg_id,
            "tg_username": self.tg_username,
            "display_name": self.display_name,
            "level": self.level,
            "xp": self.xp,
            "stats": self.stats,
            "equipment": self.equipment,
            "inventory": self.inventory,
            "pets": self.pets,
            "active_expedition": self.active_expedition,
            "completed_expeditions": self.completed_expeditions,
            "boss_kills": self.boss_kills,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Player:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ExpeditionResult:
    location_id: int
    is_boss: bool
    won: bool
    loot: List[dict] = field(default_factory=list)
    xp_gained: int = 0
    messages: List[str] = field(default_factory=list)