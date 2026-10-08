"""Экспедиции (разведывательные операции)."""

import asyncio
import random
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes

from src.config import EXPEDITIONS
from src.models import ExpeditionState
from src.storage import PlayerStorage
from src.text_manager import t

storage = PlayerStorage()


async def cmd_expedition(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/expedition – вызов подменю экспедиций."""
    tg_id = update.effective_user.id
    player = storage.load(str(tg_id))
    if not player:
        await update.effective_chat.send_message(t("not_registered"))
        return

    text = "🗺 *Отправиться на зaгадочную экспедицию?*\n\n_Ты можешь завершить её командой /expedition ещё раз, когда срок истечёт._"
    kbd = expeditions_keyboard(player.level)
    await update.effective_chat.send_message(text, reply_markup=kbd, parse_mode="Markdown")


def expeditions_keyboard(lvl: int):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    rows = []
    for key, data in EXPEDITIONS.items():
        icon = data["icon"]
        name = data["name"]
        required = data["level"]
        if lvl >= required:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"{icon} {name}", callback_data=f"exp_start:{key}"
                    )
                ]
            )
    if not rows:
        rows.append(
            [
                InlineKeyboardButton(
                    text=t("expeditions_no_avail"), callback_data="noop"
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text=t("back"), callback_data="menu_expedition")]
    )
    return InlineKeyboardMarkup(rows)


async def cb_exp_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query

    player = storage.load(str(update.effective_user.id))
    if not player:
        await query.answer(t("not_registered"), show_alert=True)
        return

    data = query.data
    key = data.split(":", 1)[1]
    exp = EXPEDITIONS.get(key)
    if not exp:
        await query.answer("❌ Неизвестная экспедиция.", show_alert=True)
        return

    if player.data.get("in_expedition"):
        await query.answer("❌ Ты уже в экспедиции!", show_alert=True)
        return

    lvl = player.level
    if lvl < exp["level"]:
        await query.answer("❌ Недостаточный уровень.", show_alert=True)
        return

    duration = exp["duration"]
    fin = int(datetime.now(timezone.utc).timestamp()) + duration
    player.data["in_expedition"] = True
    player.data["expedition_name"] = exp["name"]
    player.data["expedition_finish_time"] = fin
    player.save()

    text = (
        f"🌍 <b>Экспедиция «{exp['name']}» начата!</b>\n\n"
        f"⏳ Длительность: {duration // 3600} ч.\n"
        f"📦 Собираешь редкие ресурсы и проводишь разведку местности…\n\n"
        f"🎁 По завершении ты получишь ценные награды!"
    )

    await query.edit_message_text(
        text=text,
        parse_mode="HTML",
        reply_markup=exp_in_progress_keyboard(),
    )
    await query.answer()


def exp_in_progress_keyboard():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(text=t("finish"), callback_data="exp_finish")]]
    )


async def cb_exp_finish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query

    p = storage.load(str(update.effective_user.id))
    if not p:
        await query.answer(t("not_registered"), show_alert=True)
        return

    if not p.data.get("in_expedition"):
        await query.message.edit_text(
            "❌ Ты сейчас не в экспедиции.",
            reply_markup=None,
        )
        await query.answer()
        return

    fin = p.data.get("expedition_finish_time", 0)
    now_ts = int(datetime.now(timezone.utc).timestamp())
    if now_ts < fin:
        left = fin - now_ts
        await query.message.edit_text(
            f"⏳ Экспедиция ещё идёт. Осталось {left} сек.",
            reply_markup=query.message.reply_markup,
        )
        await query.answer()
        return

    rewards = calc_expedition_loot()
    xp = rewards["xp"]
    gold = rewards["gold"]
    loot = rewards["loot"]
    p.add_xp(xp)
    p.add_gold(gold)
    for thing in loot:
        p.add_inventory(thing)
    p.data["expeditions_count"] = p.data.get("expeditions_count", 0) + 1
    loc = p.data.get("expedition_name", "???")
    del p.data["in_expedition"]
    del p.data["expedition_finish_time"]
    del p.data["expedition_name"]
    p.save()

    text = (
        "🎉 <b>Экспедиция завершена!</b>\n\n"
        f"🌍 Локация: {loc}\n\n"
        f"📜 Краткий отчёт: Во время исследования «{loc}» ты обнаружил "
        f"древние артефакты цивилизации, оставшиеся здесь с давних времён. "
        f"Эти находки могут принести тебе немалую выгоду. "
        f"Также удалось заработать немного опыта.\n\n"
        f"💎 <b>Опыт:</b> +{xp}\n"
        f"🪙 <b>Золото:</b> +{gold}\n"
        f"🎒 <b>Добыча:</b> {', '.join(loot)}\n\n"
        "🎁 Экспедиции — отличный способ набраться сил для будущих подвигов!"
    )
    await query.message.edit_text(text, reply_markup=None)
    await query.answer()


async def cb_menu_expedition(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    text = "🗺 *Куда отправишься?*"
    keyboard = expedition_menu_keyboard()
    await update.callback_query.edit_message_text(
        text=text, reply_markup=keyboard, parse_mode="Markdown"
    )


def expedition_menu_keyboard():
    """Клавиатура меню экспедиций."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🌍 Выбрать локацию", callback_data="expedition_choose")],
            [InlineKeyboardButton("🔙 Назад", callback_data="menu_back")],
        ]
    )


def calc_expedition_loot():
    """Расчёт наград за экспедицию."""
    xp = random.randint(10, 50)
    gold = random.randint(20, 100)
    possible_loot = ["Карта сокровищ", "Древний артефакт", "Магический кристалл", "Редкое растение"]
    loot = random.sample(possible_loot, k=random.randint(0, 2))
    return {"xp": xp, "gold": gold, "loot": loot}


# ═══════════════════════════════════════════════
# ФОНОВЫЕ ЭКСПЕДИЦИИ
# ═══════════════════════════════════════════════

async def do_expedition_background(state: ExpeditionState) -> None:
    """
    Эмуляция фонового прохождения экспедиции.
    В реальном боте может выполняться через JobQueue.
    """
    await asyncio.sleep(state.duration_seconds)
    state.finish_time = datetime.now(timezone.utc).timestamp()
    state.completed = True

    if state.on_completion:
        await state.on_completion(state)