"""Модели данных: игроки, инвентарь, хранилище"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class FightType(str, Enum):
    """Тип боя."""
    STABLE = "stable"
    CRAZY = "crazy"
    AUTO = "auto"


@dataclass
class FightData:
    """Состояние текущего боя."""
    initiator_id: int | None = None
    target_id: int | None = None
    fight_type: str | None = None
    bet: int = 0
    message_id: int | None = None
    initiator_rollers: list[int] = field(default_factory=list)
    target_rollers: list[int] = field(default_factory=list)
    rounds: int = 0
    current_player: int | None = None


@dataclass
class ExpeditionState:
    """Состояние экспедиции игрока."""
    tg_id: int
    start_time: datetime
    duration_min: int
    started: bool = False
    finished: bool = False
    result_message: str = ""


@dataclass
class Player:
    """Представление игрока (соответствует PyYaml-штукам)."""

    tg_id: int
    name: str = "Викинг"
    level: int = 1
    gold: int = 0
    xp: int = 0
    hp: int = 100
    max_hp: int = 100
    inventory: dict = field(default_factory=dict)
    equipment: dict = field(default_factory=dict)
    status: str = "idle"
    dead: bool = False
    last_activity: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        """Сериализация."""
        return {
            "tg_id": self.tg_id,
            "name": self.name,
            "level": self.level,
            "gold": self.gold,
            "xp": self.xp,
            "hp": self.hp,
            "max_hp": self.max_hp,
            "inventory": self.inventory,
            "equipment": self.equipment,
            "status": self.status,
            "dead": self.dead,
            "last_activity": self.last_activity.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Player":
        """Десериализация."""
        pl = cls(
            tg_id=data["tg_id"],
            name=data.get("name", "Викинг"),
            level=data.get("level", 1),
            gold=data.get("gold", 0),
            xp=data.get("xp", 0),
            hp=data.get("hp", 100),
            max_hp=data.get("max_hp", 100),
            inventory=data.get("inventory", {}),
            equipment=data.get("equipment", {}),
            status=data.get("status", "idle"),
            dead=data.get("dead", False),
        )
        last = data.get("last_activity")
        pl.last_activity = (
            datetime.fromisoformat(last) if last else datetime.now(timezone.utc)
        )
        return pl

    def update_activity(self) -> None:
        """Обновить время последней активности."""
        self.last_activity = datetime.now(timezone.utc)

    @property
    def alive(self) -> bool:
        """Проверка что игрок жив."""
        return not self.dead

    @property
    def attack(self) -> int:
        """Базовая атака."""
        return self.level * 2 + 5

    @property
    def defense(self) -> int:
        """Базовая защита."""
        return self.level * 1 + 2

    def total_attack(self) -> int:
        """Полная атака с учётом снаряжения."""
        total = self.attack
        for item, count in self.inventory.items():
            if "ATK" in str(item):
                total += count * 2
        return total

    def total_defense(self) -> int:
        """Полная защита с учётом снаряжения."""
        total = self.defense
        for item, count in self.inventory.items():
            if "DEF" in str(item):
                total += count * 1
        return total


class UserStorage:
    """Мост для совместимости, использует Storage."""

    def __init__(self, players_file: str):
        # Отложенный импорт чтобы избежать циклических зависимостей
        from .storage import Storage

        self._storage = Storage(players_file)

    def get(self, tg_id: int) -> Player | None:
        return self._storage.load_player(tg_id)

    def load_player(self, tg_id: int) -> Player | None:
        return self._storage.load_player(tg_id)

    def save(self, player: Player) -> None:
        self._storage.save(player)

    def save_player(self, player: Player) -> None:
        self._storage.save(player)

    def load_players(self) -> dict[int, Player]:
        return self._storage.load_players()