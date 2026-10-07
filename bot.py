import logging

import nest_asyncio

from telegram import (
    BotCommand,
    BotCommandScopeAllGroupChats,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
)

from config import (
    BOT_TOKEN,
    DUEL_TIMEZONE,
)

from database import (
    init_db,
)

from handlers.utils import (
    error_handler,
    get_text,
)

from handlers.adventure import (
    adventure_command,
    adventure_callback,
    adventure_stats_command,
    adventure_inventory_command,
)
from handlers.adventure_expiration_checker import (
    expedition_expiration_job,
)


logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


BOT_COMMANDS = [
    BotCommand("adventure", get_text("menu.commands.adventure")),
    BotCommand("adventure_stats", get_text("menu.commands.adventure_stats")),
    BotCommand("adventure_inventory", get_text("menu.commands.adventure_inventory")),
]


async def post_init(application: Application):
    try:
        await application.bot.set_my_commands(
            BOT_COMMANDS,
            scope=BotCommandScopeAllGroupChats(),
        )
    except Exception:
        logger.exception("Не удалось установить команды бота")


async def main():
    nest_asyncio.apply()

    init_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # ============================================================
    # JOBS
    # ============================================================

    if application.job_queue:
        # Expedition expiration check
        application.job_queue.run_repeating(
            expedition_expiration_job,
            interval=60,
            first=60,
            name="expedition_expiration_job",
        )

    # ============================================================
    # COMMANDS
    # ============================================================

    application.add_handler(CommandHandler("adventure", adventure_command))
    application.add_handler(CommandHandler("adventure_stats", adventure_stats_command))
    application.add_handler(CommandHandler("adventure_inventory", adventure_inventory_command))

    # ============================================================
    # CALLBACKS
    # ============================================================

    application.add_handler(
        CallbackQueryHandler(
            adventure_callback,
            pattern=r"^adv_",
        )
    )

    # ============================================================
    # ERRORS
    # ============================================================

    application.add_error_handler(error_handler)

    logger.info("Bot starting...")

    await application.run_polling(
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
