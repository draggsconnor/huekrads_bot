"""Huekrads Telegram Bot – Точка входа."""

import asyncio
import logging
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from src.config import BOT_TOKEN, get_command_texts
from src.models import Player
from src.storage import Storage

# ── Боевые функции
from src.combat import cmd_fight, cb_menu_fight, cb_fight_start

# ── Экспедиционные функции
from src.expeditions import (
    cmd_expedition,
    cb_menu_expedition,
    cb_exp_start,
    cb_exp_finish,
)

# ── Планировщик
from src.scheduler import scheduler_loop

# ── Админкоманды
from src.admin_commands import (
    admin_menu,
    admin_callback,
    admin_broadcast,
    admin_broadcast_confirm,
    admin_stats,
    admin_pull,
    admin_send,
    reset_cache_callback,
    reset_cache_confirm,
    clean_players_callback,
    clean_players_confirm,
    accumulate_callback,
    save_backup,
    adventure_start,
    adventure_callback,
    admin_post_callback,
    show_journals,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)
storage = Storage()

# ═══════════════════════════════════════════════
# СОСТОЯНИЯ
# ═══════════════════════════════════════════════

MENU, ADMIN = range(2)


# ═══════════════════════════════════════════════
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ═══════════════════════════════════════════════

def main_menu_keyboard(tier, texts):
    commands = [c for c in texts if c.get("tier", 1) <= tier]
    rows = []
    for c in commands:
        label = f"{c['emoji']} {c['name']}" if "emoji" in c else c["name"]
        rows.append([InlineKeyboardButton(label, callback_data=c["command"])])
    return InlineKeyboardMarkup(rows)


async def safe_edit_or_send(update, context, text, reply_markup, new_msg=False):
    if update.effective_message and not new_msg:
        try:
            await update.effective_message.edit_text(text, reply_markup=reply_markup)
            return
        except Exception:
            pass
    if update.effective_chat:
        await update.effective_chat.send_message(text, reply_markup=reply_markup)


async def require_player(update: Update) -> Player | None:
    user_id = update.effective_user.id
    player = storage.load_player(user_id)
    if not player:
        if update.effective_chat:
            await update.effective_chat.send_message(
                "❗ Перед использованием команды отправьте /start"
            )
        return None
    player.update_activity()
    return player


# ═══════════════════════════════════════════════
# ОТОБРАЖЕНИЕ МЕНЮ (inline-клавиатура)
# ═══════════════════════════════════════════════

async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показывает inline-меню команд."""
    player = await require_player(update)
    if not player:
        return
    tx = get_command_texts()
    text = (
        f"👑 *{player.name}* | Уровень *{player.level}* | "
        f"💰 *{player.gold}* | ⭐ *{player.xp}*\n\nВыберите действие:"
    )
    reply_markup = main_menu_keyboard(player.level, tx)
    await update.effective_chat.send_message(
        text, reply_markup=reply_markup, parse_mode="Markdown"
    )


# ═══════════════════════════════════════════════
# CALLBACK-DISPATCHER
# ═══════════════════════════════════════════════

async def button_dispatcher(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("menu_admin"):
        await admin_callback(update, context)
        return

    if data == "back_to_menu":
        player = storage.load_player(query.from_user.id)
        tx = get_command_texts()
        text = (
            f"👑 *{player.name}* | Уровень *{player.level}* | "
            f"💰 *{player.gold}* | ⭐ *{player.xp}*\n\nВыберите действие:"
        )
        reply_markup = main_menu_keyboard(player.level, tx)
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")
        return

    player = await require_player(update)
    if not player:
        return

    if data.startswith("menu_fight"):
        await cb_menu_fight(update, context)
    elif data.startswith("fight_"):
        await cb_fight_start(update, context)
    elif data.startswith("menu_expedition"):
        await cb_menu_expedition(update, context)
    elif data.startswith("exp_start_"):
        await cb_exp_start(update, context)
    elif data.startswith("exp_back_"):
        await cb_exp_finish(update, context)
    elif data == "admin_stats":
        await admin_stats(update, context)
    elif data in (
        "clean_yes",
        "clean_no",
        "acc_yes",
        "acc_no",
        "reset_yes",
        "reset_no",
        "broadcast_confirm",
        "broadcast_cancel",
        "pull_confirm",
        "pull_cancel",
    ):
        await admin_callback(update, context)
    elif data.startswith("adventure_"):
        await adventure_callback(update, context)
    elif data.startswith("post_"):
        await admin_post_callback(update, context)
    elif data == "journals":
        await show_journals(update, context)
    else:
        await query.edit_message_text("✅ Действие выполнено (или ещё не реализовано).")


# ═══════════════════════════════════════════════
# КОМАНДЫ МЕНЮ / ИНВЕНТАРЬ / РЕЙТИНГ
# ═══════════════════════════════════════════════

async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/menu – открыть inline-меню."""
    player = await require_player(update)
    if not player:
        return
    await show_menu(update, context)


async def cmd_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать профиль."""
    player = await require_player(update)
    if not player:
        return
    equip = ", ".join(player.equipment.keys()) if player.equipment else "—"
    text = (
        f"👑 *{player.name}*\n"
        f"📊 Уровень: `{player.level}`\n"
        f"💰 Золото: `{player.gold}`\n"
        f"⭐ Опыт: `{player.xp}`\n"
        f"⚔️ Снаряжение: `{equip}`\n\n"
        f"_Последняя активность: {player.last_activity.strftime('%d.%m %H:%M')}_"
    )
    await update.effective_chat.send_message(text, parse_mode="Markdown")


async def cmd_inventory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать инвентарь."""
    player = await require_player(update)
    if not player:
        return
    inv = player.inventory
    if not inv:
        text = "🎒 *Инвентарь пуст.*"
    else:
        lines = []
        for tier_name, count in sorted(inv.items()):
            lines.append(f"  • {tier_name}: `x{count}`")
        text = "🎒 *Инвентарь:*\n" + "\n".join(lines)
    await update.effective_chat.send_message(text, parse_mode="Markdown")


async def cmd_top(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать топ по XP (💀 = мёртвый)."""
    players = sorted(
        storage.load_players().values(), key=lambda p: (-p.xp, p.name)
    )[:10]

    lines = ["🏆 *Рейтинг лучших*"]
    for i, p in enumerate(players, 1):
        death_icon = "💀" if p.dead else ""
        equip = f" [{'|'.join(p.equipment)}]" if p.equipment else ""
        lines.append(
            f"{i}. *{p.name}*{death_icon} — Ур. {p.level} ({p.xp} XP){equip}"
        )

    await update.effective_chat.send_message("\n".join(lines), parse_mode="Markdown")


# ═══════════════════════════════════════════════
# ОБРАБОТКА ТЕКСТА И ОТПРАВКА СООБЩЕНИЙ
# ═══════════════════════════════════════════════

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ответ на неизвестную команду."""
    await update.effective_chat.send_message(
        "❓ Неизвестная команда. Используй /menu или /help."
    )


async def admin_broadcast_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Обработка текста для бroadкаста (вызывается из ConversationHandler)."""
    return await admin_broadcast(update, context)


async def admin_admin_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """/admin — показать админку."""
    return await admin_menu(update, context)


async def admin_broadcast_confirm_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Подтверждение рассылки."""
    return await admin_broadcast_confirm(update, context)


def require_admins_only(handler):
    """Декоратор: показывает админкоманды только для ADMIN_ID."""

    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != int(context.bot_data.get("admin_id", 0)):
            return ConversationHandler.END
        return await handler(update, context)

    return wrapper


# ═══════════════════════════════════════════════
# ФОНОВЫЕ ЗАДАЧИ
# ═══════════════════════════════════════════════

async def post_init(app: Application) -> None:
    """Запуск планировщика после инициализации бота."""
    from src.config import ADMIN_ID
    app.bot_data["admin_id"] = ADMIN_ID
    asyncio.create_task(scheduler_loop(app.bot))


# ═══════════════════════════════════════════════
# КОНСТРУКЦИЯ ПРИЛОЖЕНИЯ
# ═══════════════════════════════════════════════

def main():
    application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # ── conversation для рассылки
    broadcast_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_handler)
        ],
        states={
            ADMIN: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND, admin_broadcast_confirm_handler
                )
            ]
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
    )

    # ── хендлеры команд
    application.add_handler(CommandHandler("start", unknown))
    application.add_handler(CommandHandler("menu", cmd_menu))
    application.add_handler(CommandHandler("profile", cmd_profile))
    application.add_handler(CommandHandler("inventory", cmd_inventory))
    application.add_handler(CommandHandler("top", cmd_top))
    application.add_handler(CommandHandler("fight", cmd_fight))
    application.add_handler(CommandHandler("expedition", cmd_expedition))

    # ── хендлеры кнопок
    application.add_handler(CallbackQueryHandler(adventure_callback, pattern="^adventure_"))
    application.add_handler(CallbackQueryHandler(button_dispatcher))

    # ── админка
    application.add_handler(CommandHandler("admin", admin_menu))
    application.add_handler(CommandHandler("stats", admin_stats))
    application.add_handler(CommandHandler("backup", save_backup))
    application.add_handler(CommandHandler("pull", admin_pull))
    application.add_handler(CommandHandler("send", admin_send))
    application.add_handler(CallbackQueryHandler(reset_cache_callback, pattern="^reset_"))
    application.add_handler(CallbackQueryHandler(clean_players_callback, pattern="^clean_"))
    application.add_handler(CallbackQueryHandler(accumulate_callback, pattern="^acc_"))
    application.add_handler(CallbackQueryHandler(admin_callback, pattern="^(menu_admin|broadcast|admin_)"))
    application.add_handler(broadcast_conv)

    # ── любое текстовое сообщение (fallback)
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, adventure_start)
    )

    application.run_polling()


if __name__ == "__main__":
    main()