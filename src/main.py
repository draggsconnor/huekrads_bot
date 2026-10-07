"""Точка входа в aiogram 3 бот"""

import asyncio
import logging
from os import getenv

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import BOT_TOKEN
from handlers import (
    CreateChar,
    cmd_start,
    cmd_profile,
    cmd_fight,
    cmd_expedition,
    cmd_menu,
    cb_char_create,
    cb_char_cancel,
    cb_menu_back,
    cb_menu_expedition,
    cb_exp_start,
    cb_menu_fight,
    cb_fight_start,
    cb_menu_inventory,
    cb_menu_leaderboard,
    cb_menu_help,
    cb_exp_finish,
    get_or_create,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    storage = MemoryStorage()
    bot = Bot(token=BOT_TOKEN, parse_mode=ParseMode.HTML)
    dp = Dispatcher(storage=storage)

    # ═══════════════════════════════════════
    # РЕГИСТРАЦИЯ ХЕНДЛЕРОВ
    # ═══════════════════════════════════════

    # Команды
    dp.message.register(cmd_start, Command("start"))
    dp.message.register(cmd_menu, Command("menu"))
    dp.message.register(cmd_profile, Command("profile"))
    dp.message.register(cmd_fight, Command("fight"))
    dp.message.register(cmd_expedition, Command("expedition"))

    # Callback'и — персонаж
    dp.callback_query.register(cb_char_create, F.data == "char_create", State(CreateChar.confirm))
    dp.callback_query.register(cb_char_cancel, F.data == "char_cancel", State(CreateChar.confirm))

    # Callback'и — меню
    dp.callback_query.register(cb_menu_expedition, F.data == "menu_expedition")
    dp.callback_query.register(cb_menu_fight, F.data == "menu_fight")
    dp.callback_query.register(cb_menu_inventory, F.data == "menu_inventory")
    dp.callback_query.register(cb_menu_leaderboard, F.data == "menu_leaderboard")
    dp.callback_query.register(cb_menu_help, F.data == "menu_help")

    # Подтверждения
    dp.callback_query.register(cb_exp_start, F.data == "exp_start")
    dp.callback_query.register(cb_fight_start, F.data == "fight_start")

    # Назад
    dp.callback_query.register(cb_menu_back, F.data == "menu_back")

    # ═══════════════════════════════════════
    # ПРОВЕРКА ЗАВЕРШЁННЫХ ЭКСПЕДИЦИЙ
    # ═══════════════════════════════════════
    # Стартуем вместе с ботом фоновую задачу проверки
    from asyncio import Task
    from storage import Storage as PlayerStorage
    import time

    pstorage = PlayerStorage()
    notified: set[int] = set()

    async def check_expeditions():
        while True:
            try:
                for pid_str in pstorage._load_all().keys():
                    pid = int(pid_str)
                    player = pstorage.get(pid)
                    if not player:
                        continue

                    # Если экспедиция закончилась и ещё не уведомляли
                    if player.expedition_active() is False and player.expedition_until is not None:
                        if pid not in notified:
                            notified.add(pid)
                            # Завершаем экспедицию (начисляем награду)
                            result_text = await cb_exp_finish(player)
                            await bot.send_message(
                                pid,
                                result_text,
                                reply_markup=menu_kb(),
                            )
                # Очистка уведомлений о завершившихся (у которых expedition_until уже None)
                to_remove = set()
                for pid in notified:
                    player = pstorage.get(pid)
                    if player and player.expedition_until is None:
                        to_remove.add(pid)
                notified.difference_update(to_remove)

            except Exception:
                logger.exception("Ошибка в check_expeditions")
            await asyncio.sleep(5)

    # Запускаем фоновую задачу
    task = asyncio.create_task(check_expeditions())

    # ═══════════════════════════════════════
    # СТАРТ
    # ═══════════════════════════════════════
    logger.info("Бот запускается...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

    # Останавливаем фоновую задачу при выходе
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    asyncio.run(main())