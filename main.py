import asyncio
import logging
import os

from telegram.ext import Application

from config import BOT_TOKEN
from handlers import register_handlers
from scheduler import init_scheduler

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set! Check .env file.")
        return

    application = Application.builder().token(BOT_TOKEN).build()

    # Register all handlers
    register_handlers(application)

    # Initialize expedition scheduler
    init_scheduler(application)

    logger.info("HueKrads bot started. Press Ctrl-C to stop.")
    await application.run_polling()


if __name__ == "__main__":
    asyncio.run(main())