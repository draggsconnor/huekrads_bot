"""Фоновый планировщик — восстановление HP, обработка эффектов, прочие фоновые задачи."""

import asyncio
import logging
from datetime import datetime, timezone

from src.storage import Storage

logger = logging.getLogger(__name__)
storage = Storage()


# ═══════════════════════════════════════════════════════════════
# ФОНОВЫЙ ПЛАНИРОВЩИК
# ═══════════════════════════════════════════════════════════════

async def scheduler_loop(bot):
    """Фоновая задача — запускается при старте бота."""
    while True:
        try:
            await _tick()
        except Exception as e:
            logger.error(f"Scheduler tick failed: {e}")
        await asyncio.sleep(60)  # раз в минуту


async def _tick():
    """Один тик планировщика."""
    now = datetime.now(timezone.utc)
    players = storage.load_players()

    for player in players.values():
        changed = False

        # ── Восстановление HP каждые 5 минут
        if player.last_hp_regen is not None:
            since = (now - player.last_hp_regen).total_seconds()
            if since >= 300 and player.hp < player.max_hp and not player.dead:
                player.hp = min(player.max_hp, player.hp + 1)
                player.last_hp_regen = now
                changed = True
        else:
            player.last_hp_regen = now
            changed = True

        # ── Обработка статус-эффектов (DOT, HOT, stun и т.д.)
        if player.status_effects:
            for effect in player.status_effects:
                effect.tick(player, now)
            # Удалить истёкшие
            before = len(player.status_effects)
            player.status_effects = [e for e in player.status_effects if e.expires_at > now]
            if len(player.status_effects) != before:
                changed = True

        if changed:
            storage.save_player(player)