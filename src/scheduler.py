# ==========================================
# SCHEDULER — expedition finish loop
# ==========================================

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

from aiogram import Bot

from .combat import resolve_expedition, CombatResult
from .config import LOCATIONS_DS
from .models import Player
from .storage import AsyncStorage

logger = logging.getLogger(__name__)


def humanize_duration(seconds: int) -> str:
    m, s = divmod(max(seconds, 0), 60)
    parts = []
    if m:
        parts.append(f"{m} мин.")
    if s:
        parts.append(f"{s} сек.")
    return " ".join(parts) or "0 сек."


class ExpeditionScheduler:
    def __init__(
        self,
        bot: Bot,
        storage: AsyncStorage,
        check_interval: float = 5.0,
    ):
        self.bot = bot
        self.storage = storage
        self.check_interval = check_interval
        self._task: Optional[asyncio.Task] = None
        self._cooldowns: dict[int, datetime] = {}

    # --------------------------------------------------------------
    # public API
    # --------------------------------------------------------------

    def is_on_cooldown(self, player: Player, now: datetime | None = None) -> bool:
        until = self._cooldowns.get(player.tg_id)
        if until is None:
            return False
        return (now or datetime.now(timezone.utc)) < until

    def cooldown_remaining(self, player: Player) -> int:
        until = self._cooldowns.get(player.tg_id)
        if until is None:
            return 0
        return max(int((until - datetime.now(timezone.utc)).total_seconds()), 0)

    def set_cooldown(self, player: Player, min_sec: int = 600, max_sec: int = 1800) -> int:
        seconds = random.randint(min_sec, max_sec)
        until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
        self._cooldowns[player.tg_id] = until
        return seconds

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    # --------------------------------------------------------------
    # internal loop
    # --------------------------------------------------------------

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick()
            except Exception:
                logger.exception("Scheduler tick error")
            await asyncio.sleep(self.check_interval)

    async def _tick(self) -> None:
        raw_users = await self.storage.get_all_users()
        now = datetime.now(timezone.utc)

        for raw in raw_users:
            if not raw.get("current_location"):
                continue
            started_raw = raw.get("expedition_started_at")
            if not started_raw:
                continue

            loc_id = raw["current_location"]
            loc = LOCATIONS_DS.get(loc_id)
            if loc is None:
                logger.warning("Unknown location %s for user %s", loc_id, raw.get("tg_id"))
                continue

            started_dt = datetime.fromisoformat(started_raw)
            if (now - started_dt).total_seconds() < loc.duration:
                continue  # not done yet

            player = Player(**raw)
            try:
                await self._finish_expedition(player, loc_id, loc, now)
            except Exception:
                logger.exception("Error finishing expedition for %s", player.tg_id)

    # --------------------------------------------------------------
    # expedition finish helpers
    # --------------------------------------------------------------

    async def _finish_expedition(
        self,
        player: Player,
        loc_id: int,
        loc,
        now: datetime,
    ) -> None:
        # resolve expedition logic (combat + drops)
        result = await resolve_expedition(player, loc_id, loc)

        # build message
        msg_parts = [f"🎉 Вы вернулись из {loc.name} 🌍"]

        if result.boss_name:
            msg_parts.append(f"\n⚔️ Вы встретили босса: {result.boss_name}")
            if isinstance(result.result, CombatResult):
                msg_parts.append(result.result.message)
            # drops are always present (even empty) when boss is present
            if result.drops:
                msg_parts.append("\n📦 Добыча с тела босса:")
        else:
            if result.drops:
                msg_parts.append("\n📦 Добыча:")
            else:
                msg_parts.append("\n💨 Ничего не нашли. Как всегда.")

        drop_lines: list[str] = []
        for idx, drop in enumerate(result.drops[:8], 1):
            rarity_emoji = result._rarity_level.get(drop.rarity, "⬜")
            line = f"  [{idx}] {rarity_emoji} {drop.name}"
            if drop.desc:
                line += f" — {drop.desc}"
            drop_lines.append(line)

        msg_parts.extend(drop_lines)

        if result.summary_items:
            msg_parts.append("")
            for item in result.summary_items:
                msg_parts.append(f"  {item}")

        txt = "\n".join(msg_parts)

        # update player state
        player.current_location = None
        player.expedition_started_at = None
        player.expedition_finishes_at = None
        player.completed_expeditions += 1
        if result.is_victory:
            player.boss_kills += 1

        await self.storage.save(player.tg_id, player.model_dump())

        # set next free expedition cooldown
        self.set_cooldown(player)

        # send message
        await self.bot.send_message(chat_id=player.tg_id, text=txt)