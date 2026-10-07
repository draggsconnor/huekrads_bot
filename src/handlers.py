"""Все хендлеры сообщений и callback"""

import random
import time

from aiogram import F, types, Bot
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import EXPEDITION_DURATION, ITEMS, ARTIFACTS, MONSTERS, XP_REWARDS, GOLD_REWARDS
from models import Player
from storage import Storage
import combat


# ═══════════════════════════════════════════════════════════════
# КЛАВИАТУРЫ
# ═══════════════════════════════════════════════════════════════

def menu_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="🗺 Экспедиция", callback_data="menu_expedition"),
         types.InlineKeyboardButton(text="⚔️ Сражаться", callback_data="menu_fight")],
        [types.InlineKeyboardButton(text="🎒 Инвентарь", callback_data="menu_inventory"),
         types.InlineKeyboardButton(text="🏆 Рейтинг", callback_data="menu_leaderboard")],
        [types.InlineKeyboardButton(text="❓ Помощь", callback_data="menu_help")],
    ])


def back_kb() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="◀️ Назад", callback_data="menu_back")]
    ])


def confirm_kb(yes_data: str, no_data: str = "menu_back") -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="✅ Да", callback_data=yes_data),
         types.InlineKeyboardButton(text="❌ Нет", callback_data=no_data)]
    ])


# ═══════════════════════════════════════════════════════════════
# ВСПОМОГАТЕЛЬНЫЕ
# ═══════════════════════════════════════════════════════════════

storage = Storage()


def get_or_create(user_id: int, name: str) -> Player:
    player = storage.get(user_id)
    if not player:
        player = Player(user_id=user_id, name=name)
        storage.save(player)
    return player


async def main_menu(message_or_query, text: str = "Главное меню"):
    """Вернуть в главное меню (Message или CallbackQuery)"""
    kb = menu_kb()
    if isinstance(message_or_query, Message):
        await message_or_query.answer(text, reply_markup=kb)
    else:
        await message_or_query.message.edit_text(text, reply_markup=kb)


# ═══════════════════════════════════════════════════════════════
# FSM: СОЗДАНИЕ ПЕРСОНАЖА через /start
# ═══════════════════════════════════════════════════════════════

class CreateChar(StatesGroup):
    confirm = State()


# ═══════════════════════════════════════════════════════════════
# КОМАНДЫ
# ═══════════════════════════════════════════════════════════════

async def cmd_start(message: Message, state: FSMContext):
    user_id = message.from_user.id
    name = message.from_user.full_name or "Безымянный"
    existing = storage.get(user_id)

    if existing:
        await main_menu(message, f"С возвращением, {existing.name}!\n\nТвой персонаж существует.")
        return

    await state.set_state(CreateChar.confirm)
    await message.answer(
        f"Привет, {name}! 🛡\n\n"
        f"Ты готов начать свое приключение? Создать нового персонажа?",
        reply_markup=confirm_kb("char_create", "char_cancel")
    )


async def cmd_profile(message: Message):
    player = get_or_create(message.from_user.id, message.from_user.full_name or "Безымянный")
    text = (
        f"📋 Профиль: <b>{player.name}</b>\n\n"
        f"❤️ HP: {player.hp}/{player.max_hp}\n"
        f"⚔️ Атака: {player.attack}\n"
        f"🛡 Защита: {player.defense}\n"
        f"⭐ Уровень: {player.level}\n"
        f"🔮 Опыт: {player.xp}/{player.xp_to_next}\n"
        f"💰 Золото: {player.gold}\n\n"
        f"🎒 Предметов: {len(player.items)}\n"
        f"💎 Артефактов: {len(player.artifacts)}"
    )
    await message.answer(text, parse_mode="HTML")


async def cmd_fight(message: Message):
    player = get_or_create(message.from_user.id, message.from_user.full_name or "Безымянный")

    if player.is_dead():
        await message.answer("💀 Ты мертв. Подожди 5 минут для восстановления.")
        return

    monster = random.choice(MONSTERS)
    result = combat.fight(player, monster)

    # Сохраняем результат
    storage.save(player)

    msg = f"⚔️ Ты сразился с <b>{result['monster_name']}</b>!\n\n"
    for line in result["log"]:
        msg += line + "\n"

    if result["player_won"]:
        msg += f"\n🏆 Победа!\n"
        msg += f"⭐ +{result['xp']} XP | 💰 +{result['gold']} золота"
        for lvl_msg in result["levelups"]:
            msg += f"\n{lvl_msg}"
    else:
        msg += f"\n💀 Ты проиграл. Жди 5 минут для восстановления здоровья."

    await message.answer(msg, parse_mode="HTML")


async def cmd_expedition(message: Message):
    player = get_or_create(message.from_user.id, message.from_user.full_name or "Безымянный")

    if player.is_dead():
        await message.answer("💀 Ты мертв. Не до экспедиций.")
        return

    # Если уже в экспедиции — показать статус
    if player.expedition_active():
        await message.answer(
            f"🗺 Ты в экспедиции! Вернёшься через {int(player.expedition_end() - time.time())} сек.",
            reply_markup=menu_kb()
        )
        return

    monster = random.choice(EXPEDITION_MONSTERS)
    await message.answer(
        f"🗺 Отправиться в экспедицию?\n\n"
        f"Цель: <b>{monster['name']}</b>\n"
        f"Время: {EXPEDITION_DURATION} сек.\n\n"
        f"Ты сможешь отдыхать или идти в другие экспедиции и получить награду тогда, когда она завершится без дополнительного участия.\n\n"
        f"Пока ты в экспедиции - ты можешь продолжать играть!",
        reply_markup=confirm_kb("exp_start", "menu_back"),
        parse_mode="HTML"
    )


async def cmd_menu(message: Message):
    await main_menu(message, "Главное меню")


# ═══════════════════════════════════════════════════════════════
# CALLBACK ОБРАБОТЧИКИ
# ═══════════════════════════════════════════════════════════════

async def cb_char_create(query: CallbackQuery, state: FSMContext):
    name = query.from_user.full_name or "Безымянный"
    player = Player(user_id=query.from_user.id, name=name)
    storage.save(player)
    await state.clear()
    await query.answer()
    await main_menu(query, f"✅ Персонаж <b>{name}</b> создан!\nДобро пожаловать в игру!")


async def cb_char_cancel(query: CallbackQuery, state: FSMContext):
    await state.clear()
    await query.answer()
    await query.message.edit_text("❌ Создание отменено. Напиши /start, если передумаешь.")


async def cb_menu_back(query: CallbackQuery, state: FSMContext):
    await query.answer()
    await main_menu(query, "Главное меню")


async def cb_menu_expedition(query: CallbackQuery):
    player = get_or_create(query.from_user.id, query.from_user.full_name or "Безымянный")

    if player.is_dead():
        await query.answer("💀 Ты мертв!", show_alert=True)
        return

    if player.expedition_active():
        rem = int(player.expedition_end() - time.time())
        await query.message.edit_text(
            f"🗺 Ты уже в экспедиции! Осталось {rem} сек.",
            reply_markup=back_kb()
        )
        await query.answer()
        return

    monster = random.choice(EXPEDITION_MONSTERS)
    await query.message.edit_text(
        f"🗺 Отправиться в экспедицию?\n\n"
        f"Цель: <b>{monster['name']}</b>\n"
        f"Время: <b>{EXPEDITION_DURATION} сек.</b>\n\n"
        f"Экспедиции длительные. Пока ты в экспедиции — можешь играть дальше: ходить в сражения, отдыхать и т.д.\n"
        f"Когда время истечёт — просто нажми «🗺 Экспедиция» ещё раз для награды!",
        reply_markup=confirm_kb("exp_start", "menu_back"),
        parse_mode="HTML"
    )
    await query.answer()


async def cb_exp_start(query: CallbackQuery):
    player = get_or_create(query.from_user.id, query.from_user.full_name or "Безымянный")
    if player.expedition_active():
        await query.answer("Уже в экспедиции!", show_alert=True)
        return

    player.expedition_until = time.time() + EXPEDITION_DURATION
    storage.save(player)
    await query.answer("🗺 Экспедиция началась!")
    await query.message.edit_text(
        f"🗺 Экспедиция началась! Вернёшься через <b>{EXPEDITION_DURATION}</b> сек.\n\n"
        f"Пока ждёшь — можешь драться, отдыхать и исследовать мир!",
        reply_markup=back_kb(),
        parse_mode="HTML"
    )


async def cb_exp_finish(player: Player) -> str:
    """Завершить экспедицию, вернуть текст результата."""
    import random
    reward_gold = random.randint(*GOLD_REWARDS["expedition"])
    reward_xp = random.randint(*XP_REWARDS["expedition"])

    items_found = []
    if random.random() < 0.4:
        item = random.choice(ITEMS)
        player.items.append(item)
        items_found.append(f"📜 {item}")

    artifacts_found = []
    if random.random() < 0.15:
        art = random.choice(ARTIFACTS)
        player.artifacts.append(art)
        artifacts_found.append(f"💎 {art}")

    player.gold += reward_gold
    levelups = player.add_xp(reward_xp)
    player.expedition_until = None
    storage.save(player)

    msg = (
        f"🗺 Экспедиция завершена!\n\n"
        f"⭐ +{reward_xp} XP | 💰 +{reward_gold} золота\n"
    )
    if items_found:
        msg += "\n📜 Предметы:\n" + "\n".join(items_found)
    if artifacts_found:
        msg += "\n💎 Артефакты:\n" + "\n".join(artifacts_found)
    for lvl in levelups:
        msg += f"\n{lvl}"

    return msg


async def cb_menu_fight(query: CallbackQuery):
    player = get_or_create(query.from_user.id, query.from_user.full_name or "Безымянный")

    if player.is_dead():
        await query.answer("💀 Ты мертв! Подожди 5 минут.", show_alert=True)
        return

    monster = random.choice(MONSTERS)
    await query.message.edit_text(
        f"⚔️ В бой!\n\n"
        f"Противник: <b>{monster['name']}</b>\n"
        f"❤️ HP: {monster['hp']} | ⚔️ Атака: {monster['attack']} | 🛡 Защита: {monster['defense']}\n\n"
        f"Начать сражение?",
        reply_markup=confirm_kb("fight_start", "menu_back"),
        parse_mode="HTML"
    )
    await query.answer()


async def cb_fight_start(query: CallbackQuery):
    player = get_or_create(query.from_user.id, query.from_user.full_name or "Безымянный")

    if player.is_dead():
        await query.answer("Ты мёртв!", show_alert=True)
        return

    monster = random.choice(MONSTERS)
    result = combat.fight(player, monster)
    storage.save(player)

    msg = f"⚔️ Сражение с <b>{result['monster_name']}</b>!\n\n"
    for line in result["log"]:
        msg += line + "\n"

    if result["player_won"]:
        msg += f"\n🏆 Победа!\n"
        msg += f"⭐ +{result['xp']} XP  💰 +{result['gold']} золота"
        for lvl_msg in result["levelups"]:
            msg += f"\n{lvl_msg}"
    else:
        msg += f"\n💀 Поражение! Ты мёртв. Восстановление через 5 мин."

    await query.message.edit_text(msg, reply_markup=back_kb(), parse_mode="HTML")
    await query.answer()


async def cb_menu_inventory(query: CallbackQuery):
    player = get_or_create(query.from_user.id, query.from_user.full_name or "Безымянный")

    msg = f"🎒 Инвентарь <b>{player.name}</b>\n\n"

    if player.items:
        msg += "📜 Предметы:\n"
        for item in player.items:
            msg += f"  • {item}\n"
    else:
        msg += "📜 Предметов нет\n"

    msg += "\n"
    if player.artifacts:
        msg += "💎 Артефакты:\n"
        for art in player.artifacts:
            msg += f"  • {art}\n"
    else:
        msg += "💎 Артефактов нет"

    await query.message.edit_text(msg, reply_markup=back_kb(), parse_mode="HTML")
    await query.answer()


async def cb_menu_leaderboard(query: CallbackQuery):
    players = storage.leaderboard(10)
    if not players:
        await query.message.edit_text("🏆 Пока некого показывать.", reply_markup=back_kb())
        await query.answer()
        return

    msg = "🏆 Топ игроков\n\n"
    for i, p in enumerate(players, 1):
        msg += f"{i}. <b>{p.name}</b> — Ур. {p.level} | XP: {p.xp} | 💰 {p.gold}\n"

    await query.message.edit_text(msg, reply_markup=back_kb(), parse_mode="HTML")
    await query.answer()


async def cb_menu_help(query: CallbackQuery):
    msg = (
        "❓ <b>Помощь по игре</b>\n\n"
        "<b>Команды:</b>\n"
        "/start — создать персонажа\n"
        "/menu — главное меню\n"
        "/profile — твой профиль\n"
        "/fight — быстрое сражение\n"
        "/expedition — отправиться в экспедицию\n\n"
        "<b>Кнопки меню:</b>\n"
        "🗺 Экспедиция — долгое путешествие с наградой\n"
        "⚔️ Сражаться — быстрая битва за XP и золото\n"
        "🎒 Инвентарь — твои предметы и артефакты\n"
        "🏆 Рейтинг — лучшие игроки\n\n"
        "<b>Правила:</b>\n"
        "• Проиграл? Жди 5 минут для восстановления.\n"
        "• Экспедиции идут параллельно — можешь играть дальше!\n"
        "• Собирай лут и поднимай уровень!"
    )
    await query.message.edit_text(msg, reply_markup=back_kb(), parse_mode="HTML")
    await query.answer()