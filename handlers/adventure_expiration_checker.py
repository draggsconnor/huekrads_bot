"""
Expedition expiration checker - runs periodically to check and complete expired expeditions.
"""

import logging
from datetime import datetime, timezone

from telegram.ext import ContextTypes

from handlers.adventure import (
    load_player_data,
    parse_expedition_time,
    send_expedition_result,
)

logger = logging.getLogger(__name__)


async def expedition_expiration_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Periodic job that checks for expired expeditions and sends results."""
    now = datetime.now(timezone.utc)

    try:
        player_data = load_player_data()

        for user_id, player in player_data.items():
            if not user_id.isdigit() or not isinstance(player, dict):
                continue

            active_expedition = player.get("active_expedition")
            if not isinstance(active_expedition, dict):
                continue

            end_time_str = active_expedition.get("end_time")
            if not end_time_str:
                continue

            end_time = parse_expedition_time(end_time_str)
            if end_time is None:
                continue

            if now < end_time:
                continue

            loc_id = active_expedition.get("location_id")
            username = active_expedition.get("username")
            chat_id = active_expedition.get("chat_id")
            try:
                await send_expedition_result(
                    int(user_id),
                    loc_id,
                    username,
                    context,
                    chat_id=chat_id,
                )
                logger.info("Expedition completed for user %s", user_id)
            except Exception as e:
                logger.error("Error sending expedition result for user %s: %s", user_id, e)

    except Exception as e:
        logger.error("Error in expedition_expiration_job: %s", e)
