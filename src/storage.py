# ==========================================
# STORAGE — SQLite persistence layer
# ==========================================

import json
import sqlite3
from pathlib import Path
from typing import Optional

from models import Player


DB_PATH = Path(__file__).with_name("huekrads.db")


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS players (
                tg_id INTEGER PRIMARY KEY,
                tg_username TEXT,
                display_name TEXT,
                level INTEGER NOT NULL DEFAULT 1,
                xp INTEGER NOT NULL DEFAULT 0,
                stats TEXT NOT NULL DEFAULT '{}',
                equipment TEXT NOT NULL DEFAULT '{}',
                inventory TEXT NOT NULL DEFAULT '{}',
                pets TEXT NOT NULL DEFAULT '[]',
                active_expedition TEXT,
                completed_expeditions INTEGER NOT NULL DEFAULT 0,
                boss_kills INTEGER NOT NULL DEFAULT 0,
                stat_points INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.commit()


# ---- save / load ---------------------------------------------------------

def save_player(player: Player) -> None:
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO players (
                tg_id, tg_username, display_name, level, xp,
                stats, equipment, inventory, pets,
                active_expedition, completed_expeditions, boss_kills, stat_points
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tg_id) DO UPDATE SET
                tg_username=excluded.tg_username,
                display_name=excluded.display_name,
                level=excluded.level,
                xp=excluded.xp,
                stats=excluded.stats,
                equipment=excluded.equipment,
                inventory=excluded.inventory,
                pets=excluded.pets,
                active_expedition=excluded.active_expedition,
                completed_expeditions=excluded.completed_expeditions,
                boss_kills=excluded.boss_kills,
                stat_points=excluded.stat_points
            """,
            (
                player.tg_id,
                player.tg_username,
                player.display_name,
                player.level,
                player.xp,
                json.dumps(player.stats, ensure_ascii=False),
                json.dumps(player.equipment, ensure_ascii=False),
                json.dumps(player.inventory, ensure_ascii=False),
                json.dumps(player.pets, ensure_ascii=False),
                json.dumps(player.active_expedition, ensure_ascii=False) if player.active_expedition else None,
                player.completed_expeditions,
                player.boss_kills,
                getattr(player, "stat_points", 0),
            ),
        )
        conn.commit()


def load_player(tg_id: int) -> Optional[Player]:
    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM players WHERE tg_id = ?", (tg_id,)).fetchone()
    if not row:
        return None
    return _row_to_player(row)


def _row_to_player(row: sqlite3.Row) -> Player:
    return Player(
        tg_id=row["tg_id"],
        tg_username=row["tg_username"],
        display_name=row["display_name"],
        level=row["level"],
        xp=row["xp"],
        stats=json.loads(row["stats"]),
        equipment=json.loads(row["equipment"]),
        inventory=json.loads(row["inventory"]),
        pets=json.loads(row["pets"]),
        active_expedition=json.loads(row["active_expedition"]) if row["active_expedition"] else None,
        completed_expeditions=row["completed_expeditions"],
        boss_kills=row["boss_kills"],
    )


def get_or_create(tg_id: int, username: Optional[str] = None) -> Player:
    player = load_player(tg_id)
    if player is None:
        player = Player(tg_id=tg_id, tg_username=username)
        save_player(player)
    return player