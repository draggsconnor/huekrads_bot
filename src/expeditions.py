"""Обработчики экспедиций: запуск, выбор, завершение."""

import logging
import random

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update

from src.config import EXPEDITION_DURATION
from src.models import Player
from src.scheduler import time_left
from src.storage import Storage

logger = logging.getLogger(__name__)

STORAGE = Storage()


# ──────────────────────────────────────────────────────────────────
# PUBLIC API
# ──────────────────────────────────────────────────────────────────


async def cmd_expedition(update: Update, _ctx) -> None:
    """/expedition — начать или проверить экспедицию."""
    user = update.effective_user
    if not user:
        return
    player = STORAGE.load_player(user.id)

    if player.expedition_ends is not None:
        remaining = time_left(player.expedition_ends, duration=EXPEDITION_DURATION)
        await __notify_running(update, player, remaining)
    else:
        await __show_menu(update, player)


async def cb_menu_expedition(update: Update, _ctx) -> None:
    """Callback `menu|expedition` — показать меню."""
    query = update.callback_query
    await query.answer()
    player = STORAGE.load_player(query.from_user.id)
    await __show_menu(update, player)


async def cb_exp_start(update: Update, _ctx) -> None:
    """Callback `exp_start|<idx>` — начать экспедицию."""
    query = update.callback_query
    await query.answer()

    idx = int(query.data.split("|")[1])
    player = STORAGE.load_player(query.from_user.id)

    tier = player.exp_tier_unlocked()
    options = _expedition_options(tier)

    if not (0 <= idx < len(options)):
        await query.edit_message_text("⚠️ Недопустимый вариант.")
        return

    if player.expedition_ends is not None:
        remaining = time_left(player.expedition_ends, duration=EXPEDITION_DURATION)
        await __notify_running(update, player, remaining)
        return

    # Вычитаем стоимость
    cost = options[idx].get("cost", 0)
    if player.gold < cost:
        await query.edit_message_text("💰 Недостаточно золота!")
        return

    player.gold -= cost
    player.start_expedition(options[idx]["name"])
    STORAGE.save_player(player)

    await query.edit_message_text(
        f"🗺 {options[idx]['name']} начата!\n"
        f"⏳ Вернётесь через {EXPEDITION_DURATION[tier]} мин.",
    )


async def cb_exp_finish(update: Update, _ctx) -> None:
    """Callback `exp_finish` — досрочное завершение через алмазы."""
    query = update.callback_query
    await query.answer()
    player = STORAGE.load_player(query.from_user.id)

    if player.expedition_ends is None:
        await query.edit_message_text("❌ Нет активной экспедиции.")
        return

    remaining = time_left(player.expedition_ends, duration=EXPEDITION_DURATION)
    if remaining <= 0:
        await __grant_rewards(update, player)
    else:
        cost = _rush_cost(remaining)

        if player.diamonds < cost:
            await query.edit_message_text(
                f"💎 Нужно {cost} алмазов для мгновенного завершения.",
            )
            return

        player.diamonds -= cost
        await __grant_rewards(update, player)


# ──────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────


async def __notify_running(update, player: Player, remaining: int) -> None:
    """Сообщает, что экспедиция уже идёт."""
    text = (
        f"⏳ Экспедиция «{player.expedition_name}» в пути.\n"
        f"🕒 Осталось: *{remaining}* мин."
    )
    reply_markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🏁 Завершить досрочно", callback_data="exp_finish",
                ),
            ],
            [InlineKeyboardButton("🔙 Назад", callback_data="menu|expedition")],
        ],
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=reply_markup, parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            text, reply_markup=reply_markup, parse_mode="Markdown",
        )


async def __show_menu(update: Update, player: Player) -> None:
    """Показывает доступные экспедиции по ячейкам."""
    tier = player.exp_tier_unlocked()
    options = _expedition_options(tier)

    keyboard = []
    for i, opt in enumerate(options, start=1):
        name = opt["name"]
        cost = opt.get("cost", 0)
        btn_text = f"{i}. {name}" + (f" ({cost}💰)" if cost else "")
        keyboard.append(
            [InlineKeyboardButton(btn_text, callback_data=f"exp_start|{i - 1}")],
        )

    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="menu|main")])

    text = "🗺 *Выберите экспедицию:*\n\n" + __expedition_info(options, tier)

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown",
        )


async def __grant_rewards(update: Update, player: Player) -> None:
    """Выдаёт награду и завершает экспедицию."""
    tier = player.exp_tier_unlocked()
    options = _expedition_options(tier)
    expedition = next(
        (o for o in options if o["name"] == player.expedition_name), options[0],
    )

    gold = expedition.get("gold", 0) + random.randint(0, expedition.get("gold_var", 0))
    xp = expedition.get("xp", 0) + random.randint(0, expedition.get("xp_var", 0))
    loot = expedition.get("loot", [])

    player.gold += gold
    player.xp += xp
    drops: list[str] = []
    for item, chance in loot:
        if random.random() < chance:
            player.inventory.setdefault(item, 0)
            player.inventory[item] += 1
            drops.append(item)

    elapsed = player.expedition_duration_min()
    player.finish_expedition()

    lines = [f"🎉 Экспедиция завершена за *{elapsed}* мин!"]
    lines.append(f"💰 Золото: +{gold}")
    lines.append(f"⭐ Опыт: +{xp}")

    if drops:
        lines.append("🎁 Лут: " + ", ".join(drops))
    else:
        lines.append("🎁 Лута нет")

    player.add_log("; ".join(lines))
    STORAGE.save_player(player)

    reply_markup = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🗺 Ещё экспедиция", callback_data="menu|expedition")],
            [InlineKeyboardButton("🔙 В меню", callback_data="menu|main")],
        ],
    )

    text = "\n".join(lines)

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=reply_markup, parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            text, reply_markup=reply_markup, parse_mode="Markdown",
        )


# ──────────────────────────────────────────────────────────────────
# DATA
# ──────────────────────────────────────────────────────────────────


def __expedition_info(options: list[dict], tier: int) -> str:
    """Описание доступных экспедиций."""
    duration = EXPEDITION_DURATION[tier]
    info_lines: list[str] = []
    for opt in options:
        name = opt["name"]
        cost = opt.get("cost", 0)
        gold = opt.get("gold", 0)
        xp = opt.get("xp", 0)
        info_lines.append(
            f"• {name} — {duration} мин, {gold}💰, {xp}⭐"
            + (f", стоимость {cost}💰" if cost else ""),
        )
    return "\n".join(info_lines)


def _expedition_options(tier: int) -> list[dict]:
    """Варианты экспедиций по тиру."""
    if tier == 2:
        return [
            {"name": "Древний храм", "gold": 30, "gold_var": 10, "xp": 25, "xp_var": 5, "cost": 15, "loot": [("Реликвия", 0.25)]},
            {"name": "Туманный лес", "gold": 25, "gold_var": 8, "xp": 22, "xp_var": 4, "loot": [("Эликсир", 0.35)]},
        ]
    if tier == 1:
        return [
            {"name": "Заброшенная шахта", "gold": 15, "gold_var": 5, "xp": 12, "xp_var": 3, "loot": [("Железо", 0.4)]},
            {"name": "Лабиринт крыс", "gold": 12, "gold_var": 4, "xp": 10, "xp_var": 2, "loot": [("Сыр", 0.6)]},
        ]
    return [
        {"name": "Лесная опушка", "gold": 5, "gold_var": 2, "xp": 5, "xp_var": 1, "loot": [("Гриб", 0.5)]},
        {"name": "Старый мост", "gold": 4, "gold_var": 2, "xp": 4, "xp_var": 1, "loot": []},
    ]


def _rush_cost(minutes_remaining: int) -> int:
    """Стоимость досрочного завершения (1 алмаз за 5 минут)."""
    return max(1, minutes_remaining // 5 + (1 if minutes_remaining % 5 else 0))