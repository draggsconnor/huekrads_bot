"""Простое файловое хранилище игроков"""

import json
import os
from typing import Optional
from .config import PLAYERS_FILE
from .models import Player


class Storage:
    """Хранилище данных игроков в JSON"""

    def __init__(self, filepath: str = PLAYERS_FILE):
        self.filepath = filepath
        self._ensure_dir()

    def _ensure_dir(self) -> None:
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)

    def _load_all(self) -> dict[str, dict]:
        if not os.path.exists(self.filepath):
            return {}
        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

    def _save_all(self, data: dict[str, dict]) -> None:
        with open(self.filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get(self, user_id: int) -> Optional[Player]:
        """Получить игрока по ID (старый метод)."""
        return self.load_player(user_id)

    def load_player(self, user_id: int) -> Optional[Player]:
        all_players = self._load_all()
        raw = all_players.get(str(user_id))
        if raw:
            return Player.from_dict(raw)
        return None

    def save(self, player: Player) -> None:
        """Сохранить игрока (старый метод)."""
        all_players = self._load_all()
        all_players[str(player.tg_id)] = player.to_dict()
        self._save_all(all_players)

    def save_player(self, player: Player) -> None:
        """Сохранить игрока."""
        self.save(player)

    def delete(self, user_id: int) -> None:
        all_players = self._load_all()
        all_players.pop(str(user_id), None)
        self._save_all(all_players)

    def leaderboard(self, limit: int = 10) -> list[Player]:
        all_players = self._load_all()
        players = [Player.from_dict(v) for v in all_players.values()]
        players.sort(key=lambda p: (p.level, p.xp), reverse=True)
        return players[:limit]

    def load_players(self) -> dict[int, Player]:
        """Загрузить всех игроков как словарь {user_id: Player}."""
        all_players = self._load_all()
        return {int(k): Player.from_dict(v) for k, v in all_players.items()}


# Удобные функции на уровне модуля
def save_player(player: Player) -> None:
    """Сохранить игрока через глобальное хранилище."""
    storage = Storage()
    storage.save(player)