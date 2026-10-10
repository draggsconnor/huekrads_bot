import logging

from telegram import BotCommand, BotCommandScopeAllGroupChats, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from config import BOT_TOKEN
from database import init_db
from handlers.adventure import (
    adventure_callback,
    adventure_command,
    adventure_inventory_command,
    adventure_stats_command,
)
from handlers.adventure_expiration_checker import expedition_expiration_job
from handlers.utils import error_handler
from text_resources import get_text

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


BOT_COMMANDS = [
    BotCommand("start", get_text("menu.commands.start")),
    BotCommand("help", get_text("menu.commands.help")),
    BotCommand("adventure", get_text("menu.commands.adventure")),
    BotCommand("adventure_stats", get_text("menu.commands.adventure_stats")),
    BotCommand("adventure_inventory", get_text("menu.commands.adventure_inventory")),
]


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.effective_message.reply_text(get_text("menu.help_text"))


async def post_init(application: Application) -> None:
    try:
        await application.bot.set_my_commands(BOT_COMMANDS)
        await application.bot.set_my_commands(
            BOT_COMMANDS,
            scope=BotCommandScopeAllGroupChats(),
        )
    except Exception:
        logger.exception("Не удалось установить команды бота")


def main() -> None:
    if not BOT_TOKEN:
        raise SystemExit("BOT_TOKEN не задан. Скопируйте .env.example в .env и укажите токен.")

    init_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    if application.job_queue:
        application.job_queue.run_repeating(
            expedition_expiration_job,
            interval=60,
            first=15,
            name="expedition_expiration_job",
        )
    else:
        logger.error(
            "JobQueue недоступен: экспедиции не закроются по таймеру. "
            "Установите python-telegram-bot[job-queue]."
        )

    application.add_handler(CommandHandler("start", adventure_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("adventure", adventure_command))
    application.add_handler(CommandHandler("adventure_stats", adventure_stats_command))
    application.add_handler(CommandHandler("adventure_inventory", adventure_inventory_command))
    application.add_handler(
        CallbackQueryHandler(
            adventure_callback,
            pattern=r"^adv_",
        )
    )
    application.add_error_handler(error_handler)

    logger.info("Bot starting...")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
