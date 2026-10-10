"""Боевые команды и обработчики — бои с монстрами и боссами."""

import logging
import random

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from .config import ITEMS, MONSTERS, XP_REWARDS
from .models import FightData, FightType, Player
from .storage import Storage
from .utils import r2

logger = logging.getLogger(__name__)
storage = Storage()


# ═══════════════════════════════════════════════════════════════
# /fight  — запуск боя
# ═══════════════════════════════════════════════════════════════

async def cmd_fight(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Текстовая команда /fight."""
    user = update.effective_user
    if not user:
        return

    player = storage.load_player(user.id)
    if player.hp <= 0:
        if update.message:
            await update.message.reply_text("❌ Ты мёртв. Воскресни в меню.")
        return

    enemy_conf = random.choice(MONSTERS)
    enemy = Player.from_dict(enemy_conf)
    enemy.hp = enemy.max_hp

    data = FightData(enemy=enemy, type=FightType.STANDARD)

    # Опционально: давать опыт за победу над этим врагом
    data.xp_reward = XP_REWARDS.get(enemy.name, 0)

    msg = f"⚔️ {player.name} вступает в бой с {enemy.name}!"

    keyboard = [
        [
            InlineKeyboardButton("🗡 Атаковать", callback_data="fight_start"),
            InlineKeyboardButton("🏃 Сбежать", callback_data="fight_flee"),
        ]
    ]

    if update.message:
        await update.message.reply_text(
            msg, reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        # fallback
        await update.effective_chat.send_message(
            msg, reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def cb_menu_fight(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Кнопка « Бой» в главном меню."""
    query = update.callback_query
    if query:
        await query.answer()
    return await cmd_fight(update, ctx)


# ═══════════════════════════════════════════════════════════════
# Обработка инлайн-кнопок боя
# ═══════════════════════════════════════════════════════════════

async def cb_fight_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Нажали «Атаковать»."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    player = storage.load_player(user.id)

    if player.hp <= 0:
        await query.edit_message_text("💀 Ты уже мёртв.")
        return

    # Восстанавливаем HP врага на всякий случай
    enemy = Player.from_dict(random.choice(MONSTERS))
    enemy.hp = enemy.max_hp

    data = FightData(enemy=enemy, type=FightType.STANDARD)
    data.xp_reward = XP_REWARDS.get(enemy.name, 0)

    # Простой бой — один раунд
    pdmg = random.randint(enemy.min_damage, enemy.max_damage)
    edmg = random.randint(player.min_damage, player.max_damage)

    player.hp -= pdmg
    enemy.hp -= edmg

    # Лог
    log_lines = []

    # Фразы
    p_phrase = random.choice(enemy.phrases) if enemy.phrases else "Атакует!"
    e_phrase = random.choice(player.phrases) if player.phrases else "Атакует!"
    log_lines.append(r2(p_phrase) if hasattr(r2, "__call__") else p_phrase)
    log_lines.append(r2(e_phrase) if hasattr(r2, "__call__") else e_phrase)

    if player.hp <= 0:
        player.hp = 0
        player.dead = True
        result = f"💀 Ты пал в бою с {enemy.name}!"
        storage.save_player(player)
        await query.edit_message_text(f"{result}\n\n" + "\n".join(log_lines))
        return

    if enemy.hp <= 0:
        # Победа
        xp = data.xp_reward
        if xp:
            player.add_xp(xp)
        player.kills += 1

        # С небольшим шансом — лут
        loot = None
        if random.random() < 0.3:
            loot = random.choice(ITEMS)
            player.inventory.append(loot["key"])
            log_lines.append(f"🎁 Трофей: {loot['name']}")

        storage.save_player(player)

        result = f"🏆 Победа над {enemy.name}!"
        if xp:
            result += f" (+{xp} XP)"
        if player.kills % 10 == 0:
            result += "\n✨ Достижение: 10 побед подряд!"

        await query.edit_message_text(
            f"{result}\n\n" + "\n".join(log_lines),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⚔️ Следующий бой", callback_data="fight_start")],
                [InlineKeyboardButton("🔙 В меню", callback_data="menu_back")]
            ])
        )
        return

    # Бой продолжается
    storage.save_player(player)

    msg = (
        f"⚔️ Бой с {enemy.name}!\n\n"
        f"Твой HP: {player.hp}/{player.max_hp}\n"
        f"Враг HP: {enemy.hp}/{enemy.max_hp}\n\n"
        + "\n".join(log_lines)
    )

    await query.edit_message_text(
        msg,
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🗡 Атаковать", callback_data="fight_start"),
                InlineKeyboardButton("🏃 Сбежать", callback_data="fight_flee"),
            ]
        ])
    )


async def cb_fight_flee(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Сбежать из боя."""
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "🏃 Ты сбежал с поля боя.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 В меню", callback_data="menu_back")]
        ])
    )