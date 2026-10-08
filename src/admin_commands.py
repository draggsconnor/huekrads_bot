"""Admin-команды и хендлеры."""

import asyncio
import logging

from aiogram import Router, types, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from src.config import ADMIN_ID
from src.storage import Storage

logger = logging.getLogger(__name__)
router = Router(name="admin")
storage = Storage()


# ═══════════════════════════════════════════════════════════════
# STATES
# ═══════════════════════════════════════════════════════════════

class BroadcastState(StatesGroup):
    text = State()


# ═══════════════════════════════════════════════════════════════
# COMMANDS
# ═══════════════════════════════════════════════════════════════

@router.message(F.text.startswith("/kick"))
async def cmd_kick(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Только для администратора.")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("⚠️ Использование: `/kick <user_id или @username>`")
        return
    target = parts[1].strip().lstrip("@")
    players = storage.load_players()
    found = None
    for uid, p in players.items():
        if str(uid) == target or (p.name and p.name.lower() == target.lower()):
            found = uid
            break
    if not found:
        await message.answer("❌ Игрок не найден.")
        return
    del players[found]
    storage.save_all_players(players)
    logger.info("Admin %s kicked player %s", message.from_user.id, found)
    await message.answer(f"🥾 Игрок `{found}` удалён.")


@router.message(F.text.startswith("/ban"))
async def cmd_ban(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Только для администратора.")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("⚠️ Использование: `/ban <user_id или @username>`")
        return
    target = parts[1].strip().lstrip("@")
    players = storage.load_players()
    found = None
    for uid, p in players.items():
        if str(uid) == target or (p.name and p.name.lower() == target.lower()):
            found = uid
            break
    if not found:
        await message.answer("❌ Игрок не найден.")
        return
    players[found].banned = True
    storage.save_player(players[found])
    logger.info("Admin %s banned player %s", message.from_user.id, found)
    await message.answer(f"🔒 Игрок `{found}` заблокирован.")


@router.message(F.text.startswith("/unban"))
async def cmd_unban(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Только для администратора.")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("⚠️ Использование: `/unban <user_id или @username>`")
        return
    target = parts[1].strip().lstrip("@")
    players = storage.load_players()
    found = None
    for uid, p in players.items():
        if str(uid) == target or (p.name and p.name.lower() == target.lower()):
            found = uid
            break
    if not found:
        await message.answer("❌ Игрок не найден.")
        return
    players[found].banned = False
    storage.save_player(players[found])
    logger.info("Admin %s unbanned player %s", message.from_user.id, found)
    await message.answer(f"🔓 Игрок `{found}` разблокирован.")


@router.message(F.text == "/broadcast")
async def cmd_broadcast(message: Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Только для администратора.")
        return
    await state.set_state(BroadcastState.text)
    await message.answer("📝 Введите текст для рассылки всем игрокам:")


# ═══════════════════════════════════════════════════════════════
# CALLBACKS
# ═══════════════════════════════════════════════════════════════

@router.callback_query(F.data == "menu_back", BroadcastState.text)
async def cb_broadcast_cancel(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Рассылка отменена.")
    await callback.answer()


# ═══════════════════════════════════════════════════════════════
# BROADCAST MESSAGE HANDLER
# ═══════════════════════════════════════════════════════════════

@router.message(BroadcastState.text)
async def process_broadcast(message: Message, state: FSMContext, bot):
    broadcast_text = message.text
    players = storage.load_players()
    sent = 0
    failed = 0
    for uid in list(players.keys()):
        try:
            await bot.send_message(uid, f"📢 *Сообщение от администрации:*\n\n{broadcast_text}", parse_mode="Markdown")
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1
    await state.clear()
    await message.answer(f"✅ Рассылка завершена: отправлено {sent}, не удалось {failed}.")