# ==========================================
# HANDLERS — all Telegram command/callback handlers
# ==========================================

import asyncio
from datetime import datetime, timedelta
from typing import Optional

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

from config import (
    ADMINS,
    ITEMS,
    LOCATION_NAMES,
    FOOL_LOCATIONS,
    MANIAC_LOCATIONS,
    NOTEBOOK_LOCATIONS,
    PET_SHOP,
    START_INVENTORY,
)
from models import ExpeditionResult, Player
from storage import (
    get_location_info,
    get_or_create_user,
    reset_user_data,
    save_location_info,
    save_user,
)
from combat import run_auto_battle, roll_pet
from scheduler import (
    cancel_timer,
    is_on_expedition,
    seconds_remaining,
    start_expedition_timer,
)

# ------------------------------------------------------------------ #
# States for admin/change context
# ------------------------------------------------------------------ #
(
    CHOOSING_EXPEDITION,
    CHOOSING_SETTINGS,
    CHANGING_PSEUDONYM,
    CHANGING_BIO,
    CHANGING_AVATAR,
    BUYING_PET,
    ADMIN_EDIT_HP,
    ADMIN_EDIT_XP,
    ADMIN_EDIT_ITEM,
    ADMIN_GIVE_ITEM_ID,
    ADMIN_GIVE_ITEM_QTY,
) = range(11)

# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #


def _kb(buttons: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    """Build InlineKeyboardMarkup from list of (text, callback_data) rows."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(t, callback_data=d) for t, d in row]
            for row in buttons
        ]
    )


async def _send_or_edit(
    update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, keyboard=None
):
    """Send new message if callback, otherwise reply."""
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, reply_markup=keyboard
        )
    else:
        await update.effective_message.reply_text(text, reply_markup=keyboard)


def _admin_check(user_id: int) -> bool:
    return user_id in ADMINS


# ------------------------------------------------------------------ #
# /start
# ------------------------------------------------------------------ #


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    player = get_or_create_user(user.id, user.username or "Аноним")
    text = (
        f"👋 Привет, {player.pseudonym}!\n\n"
        f"Ты в ХьюКрадс — ламповой текстовой RPG.\n\n"
        f"• Отправь /menu чтобы открыть главное меню\n"
        f"• Используй /expedition чтобы отправиться в поход\n"
    )
    await update.effective_message.reply_text(text)


# ------------------------------------------------------------------ #
# /menu (inline main menu)
# ------------------------------------------------------------------ #


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    player = get_or_create_user(
        update.effective_user.id, update.effective_user.username or "Аноним"
    )
    text = (
        f"🏠 Главное меню — {player.pseudonym}\n"
        f"HP: {player.hp}/{player.max_hp} | XP: {player.xp} | Ур. {player.level}\n"
    )

    keyboard = _kb(
        [
            [("⚔️ Экспедиция", "menu_expedition")],
            [("👤 Профиль", "menu_profile"), ("🐶 Звери", "menu_pets")],
            [("📦 Инвентарь", "menu_inventory"), ("⚙️ Настройки", "menu_settings")],
        ]
    )
    if _admin_check(update.effective_user.id):
        keyboard.inline_keyboard.append(
            [InlineKeyboardButton("🔧 Админ-панель", callback_data="menu_admin")],
        )

    await _send_or_edit(update, context, text, keyboard)


# ------------------------------------------------------------------ #
# Expedition flow
# ------------------------------------------------------------------ #


async def expedition_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point: choose location category."""
    player = get_or_create_user(
        update.effective_user.id, update.effective_user.username or "Аноним"
    )
    keyboard = _kb(
        [
            [("🤡 Глупый контекст", "cat_fool")],
            [("🔪 Маньяк-убийца", "cat_maniac")],
            [("📓 Распределитель тетрадей", "cat_notebook")],
            [("🏠 Главное меню", "back_menu")],
        ]
    )
    await _send_or_edit(
        update,
        context,
        "Куда отправляемся в экспедицию?",
        keyboard,
    )
    return CHOOSING_EXPEDITION


async def _expedition_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cat = query.data  # cat_fool / cat_maniac / cat_notebook

    mapping = {
        "cat_fool": (FOOL_LOCATIONS, "Глупый контекст"),
        "cat_maniac": (MANIAC_LOCATIONS, "Маньяк-убийца"),
        "cat_notebook": (NOTEBOOK_LOCATIONS, "Распределитель тетрадей"),
    }
    locs, title = mapping[cat]

    buttons = []
    for loc_id, loc_name in locs.items():
        buttons.append([(loc_name, f"loc_{loc_id}")])
    buttons.append([("🔙 Назад", "back_exp")])

    await query.edit_message_text(
        f"Выбери локацию ({title}):",
        reply_markup=_kb(buttons),
    )
    return CHOOSING_EXPEDITION


async def _expedition_go(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    loc_id = int(query.data.replace("loc_", ""))
    tg_id = update.effective_user.id
    player = get_or_create_user(tg_id, update.effective_user.username or "Аноним")

    if is_on_expedition(player):
        rem = seconds_remaining(player)
        mins, secs = divmod(rem, 60)
        await query.edit_message_text(
            f"⏳ Уже в экспедиции! Осталось {mins}м {secs}с.",
            reply_markup=_kb([[("🏠 Меню", "back_menu")]]),
        )
        return ConversationHandler.END

    # Start timer & send immediate confirmation
    start_expedition_timer(player, loc_id, callback=_on_expedition_complete)
    save_user(player)

    await query.edit_message_text(
        f"🚀 Экспедиция началась!\n"
        f"Локация: {LOCATION_NAMES[loc_id]}\n"
        f"Я напишу, когда вернёшься.",
        reply_markup=_kb([[("🏠 Меню", "back_menu")]]),
    )
    return ConversationHandler.END


async def _on_expedition_complete(player: Player, result: ExpeditionResult):
    """Callback fired by scheduler when expedition timer expires."""
    # Award XP
    player.xp += result.xp_gained
    player.check_level_up()

    # Award loot items
    for item in result.loot:
        player.inventory[item["id"]] = player.inventory.get(item["id"], 0) + item["quantity"]

    # Heal slightly after expedition
    player.hp = min(player.max_hp, int(player.hp + player.max_hp * 0.3))

    save_user(player)

    # Build message
    lines = [
        f"🎒 Экспедиция окончена!\n",
        *result.messages,
        f"\n💚 HP восстановлено до {player.hp}/{player.max_hp}",
        f"⭐ XP: {player.xp}",
    ]
    text = "\n".join(lines)

    # Notify via JobQueue (safe to call from outside handler context)
    # Wait... JobQueue requires application reference. We'll store app in bot_data.
    app: Optional[Application] = None
    # Because we're in a callback without Update, we can use a global ref or pass app.
    return text, player.tg_id


# ------------------------------------------------------------------ #
# Profile / Inventory / Pets
# ------------------------------------------------------------------ #


async def profile_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    player = get_or_create_user(
        update.effective_user.id, update.effective_user.username or "Аноним"
    )
    loc_name = LOCATION_NAMES.get(player.context_id, "Неизвестно")
    text = (
        f"👤 {player.pseudonym}\n"
        f"📝 Био: {player.bio or '—'}\n"
        f"❤️ HP: {player.hp}/{player.max_hp}\n"
        f"⭐ XP: {player.xp} (ур. {player.level})\n"
        f"🌍 Контекст: {loc_name}\n"
        f"🎒 Выполнено экспедиций: {player.completed_expeditions}\n"
        f"👹 Боссов убито: {player.boss_kills}\n"
        f"🐶 Зверей: {len(player.pets)}\n"
    )
    await _send_or_edit(
        update,
        context,
        text,
        _kb([[("🏠 Меню", "back_menu")]]),
    )


async def inventory_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    player = get_or_create_user(
        update.effective_user.id, update.effective_user.username or "Аноним"
    )
    if not player.inventory:
        text = "📦 Инвентарь пуст."
    else:
        lines = ["📦 Инвентарь:\n"]
        for item_id, qty in player.inventory.items():
            item = ITEMS.get(item_id)
            name = item["name"] if item else item_id
            lines.append(f"• {name} x{qty}")
        text = "\n".join(lines)
    await _send_or_edit(
        update,
        context,
        text,
        _kb([[("🏠 Меню", "back_menu")]]),
    )


async def pets_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    player = get_or_create_user(
        update.effective_user.id, update.effective_user.username or "Аноним"
    )
    if not player.pets:
        text = "У тебя пока нет зверей.\nЗаставь глупого контекста сдать свою тетрадь и приручи его!"
    else:
        lines = ["🐶 Твои звери:\n"]
        for idx, pet in enumerate(player.pets, 1):
            active = " 🟢" if pet.get("companion") else ""
            lines.append(f"{idx}. {pet['name']}{active}")
            if pet.get("companion"):
                lines.append(f"   (активный компаньон)")
        text = "\n".join(lines)

    keyboard = _kb(
        [
            [("🏠 Меню", "back_menu")],
        ]
    )
    await _send_or_edit(update, context, text, keyboard)


# ------------------------------------------------------------------ #
# Settings
# ------------------------------------------------------------------ #


async def settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send_or_edit(
        update,
        context,
        "⚙️ Настройки персонажа:",
        _kb(
            [
                [("✏️ Псевдоним", "set_pseudonym"), ("📝 Био", "set_bio")],
                [("🖼 Аватар", "set_avatar"), ("🔄 Сброс", "set_reset")],
                [("🏠 Меню", "back_menu")],
            ]
        ),
    )
    return CHOOSING_SETTINGS


async def _set_pseudonym_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send_or_edit(update, context, "Введи новый псевдоним:")
    return CHANGING_PSEUDONYM


async def _set_pseudonym_recv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    player = get_or_create_user(
        update.effective_user.id, update.effective_user.username or "Аноним"
    )
    player.pseudonym = update.effective_message.text.strip()[:32]
    save_user(player)
    await update.effective_message.reply_text(
        f"✅ Псевдоним обновлён: {player.pseudonym}"
    )
    return ConversationHandler.END


async def _set_bio_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send_or_edit(update, context, "Введи новую биографию:")
    return CHANGING_BIO


async def _set_bio_recv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    player = get_or_create_user(
        update.effective_user.id, update.effective_user.username or "Аноним"
    )
    player.bio = update.effective_message.text.strip()[:256]
    save_user(player)
    await update.effective_message.reply_text("✅ Био обновлено.")
    return ConversationHandler.END


async def _set_avatar_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send_or_edit(
        update,
        context,
        "Отправь новый аватар (фото):",
    )
    return CHANGING_AVATAR


async def _set_avatar_recv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    player = get_or_create_user(
        update.effective_user.id, update.effective_user.username or "Аноним"
    )
    photo = update.effective_message.photo[-1] if update.effective_message.photo else None
    if photo:
        player.avatar_file_id = photo.file_id
        save_user(player)
        await update.effective_message.reply_text("✅ Аватар обновлён.")
    else:
        await update.effective_message.reply_text("⚠️ Это не фото.")
    return ConversationHandler.END


async def _set_reset_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = (
        "⚠️ Точно сбросить прогресс?\n"
        "Все данные будут удалены безвозвратно."
    )
    await query.edit_message_text(
        text,
        reply_markup=_kb(
            [
                [("🔴 Да, сбросить", "confirm_reset")],
                [("🔙 Назад", "back_settings")],
            ]
        ),
    )


async def _set_reset_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    reset_user_data(update.effective_user.id)
    await query.edit_message_text(
        "🗑 Прогресс сброшен.",
        reply_markup=_kb([[("🏠 Меню", "back_menu")]]),
    )


# ------------------------------------------------------------------ #
# Admin panel
# ------------------------------------------------------------------ #


async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _admin_check(update.effective_user.id):
        await _send_or_edit(update, context, "⛔ Нет доступа.")
        return ConversationHandler.END

    await _send_or_edit(
        update,
        context,
        "🔧 Админ-панель:",
        _kb(
            [
                [("📊 Статистика", "admin_stats")],
                [("💊 Изменить HP", "admin_hp"), ("⭐ Изменить XP", "admin_xp")],
                [("🎁 Выдать предмет", "admin_give")],
                [("🏠 Меню", "back_menu")],
            ]
        ),
    )


async def _admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    from storage import get_all_users

    users = get_all_users()
    lines = [f"👥 Всего игроков: {len(users)}\n"]
    for u in users:
        lines.append(f"• {u.pseudonym} (ур.{u.level}, XP {u.xp})")
    text = "\n".join(lines[:50])  # cap
    await query.edit_message_text(
        text, reply_markup=_kb([[("🔙 Админ", "menu_admin")], [("🏠 Меню", "back_menu")]])
    )


async def _admin_hp_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["admin_edit"] = "hp"
    await query.edit_message_text(
        "Введи ID игрока и новое HP через пробел (например: 12345 50):"
    )
    return ADMIN_EDIT_HP


async def _admin_hp_recv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = update.effective_message.text.strip().split()
    if len(parts) != 2 or not parts[1].isdigit():
        await update.effective_message.reply_text("⚠️ Неверный формат.")
        return ADMIN_EDIT_HP
    tid, val = int(parts[0]), int(parts[1])
    player = get_or_create_user(tid, f"user_{tid}")
    player.hp = max(0, min(player.max_hp, val))
    save_user(player)
    await update.effective_message.reply_text(
        f"✅ HP для {player.pseudonym}: {player.hp}/{player.max_hp}"
    )
    return ConversationHandler.END


async def _admin_xp_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["admin_edit"] = "xp"
    await query.edit_message_text(
        "Введи ID игрока и новое XP через пробел:"
    )
    return ADMIN_EDIT_XP


async def _admin_xp_recv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = update.effective_message.text.strip().split()
    if len(parts) != 2 or not parts[1].isdigit():
        await update.effective_message.reply_text("⚠️ Неверный формат.")
        return ADMIN_EDIT_XP
    tid, val = int(parts[0]), int(parts[1])
    player = get_or_create_user(tid, f"user_{tid}")
    player.xp = val
    player.check_level_up()
    save_user(player)
    await update.effective_message.reply_text(
        f"✅ XP для {player.pseudonym}: {player.xp} (ур. {player.level})"
    )
    return ConversationHandler.END


async def _admin_give_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Введи ID игрока:")
    return ADMIN_GIVE_ITEM_ID


async def _admin_give_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tid_txt = update.effective_message.text.strip()
    if not tid_txt.isdigit():
        await update.effective_message.reply_text("⚠️ ID должен быть числом.")
        return ADMIN_GIVE_ITEM_ID
    context.user_data["give_tid"] = int(tid_txt)
    item_list = "\n".join(f"{k}: {v['name']}" for k, v in ITEMS.items())
    await update.effective_message.reply_text(f"Введи ID предмета:\n{item_list}")
    return ADMIN_GIVE_ITEM_QTY


async def _admin_give_qty(update: Update, context: ContextTypes.DEFAULT_TYPE):
    item_id = update.effective_message.text.strip()
    if item_id not in ITEMS:
        await update.effective_message.reply_text("⚠️ Неверный ID предмета.")
        return ADMIN_GIVE_ITEM_QTY
    context.user_data["give_item_id"] = item_id
    await update.effective_message.reply_text("Введи количество:")
    return ADMIN_GIVE_ITEM_QTY + 1  # trick to reuse same function... no, easier separate
    # We'll handle qty in another handler below (ADMIN_GIVE_QTY)


# ------------------------------------------------------------------ #
# Back navigation helpers
# ------------------------------------------------------------------ #


async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await cmd_menu(update, context)
    return ConversationHandler.END


async def back_to_expedition(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await expedition_start(update, context)


async def back_to_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await settings_menu(update, context)


async def back_to_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await admin_menu(update, context)


# ------------------------------------------------------------------ #
# Register handlers on Application
# ------------------------------------------------------------------ #


def register_handlers(application: Application):
    # Simple commands
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("menu", cmd_menu))

    # Expedition conversation
    exp_conv = ConversationHandler(
        entry_points=[
            CommandHandler("expedition", expedition_start),
            CallbackQueryHandler(expedition_start, pattern="^menu_expedition$"),
        ],
        states={
            CHOOSING_EXPEDITION: [
                CallbackQueryHandler(_expedition_category, pattern="^cat_"),
                CallbackQueryHandler(_expedition_go, pattern="^loc_"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(back_to_menu, pattern="^back_menu$"),
            CallbackQueryHandler(back_to_expedition, pattern="^back_exp$"),
        ],
    )
    application.add_handler(exp_conv)

    # Settings conversation
    settings_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(settings_menu, pattern="^menu_settings$"),
        ],
        states={
            CHOOSING_SETTINGS: [
                CallbackQueryHandler(_set_pseudonym_start, pattern="^set_pseudonym$"),
                CallbackQueryHandler(_set_bio_start, pattern="^set_bio$"),
                CallbackQueryHandler(_set_avatar_start, pattern="^set_avatar$"),
                CallbackQueryHandler(_set_reset_confirm, pattern="^set_reset$"),
            ],
            CHANGING_PSEUDONYM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, _set_pseudonym_recv),
            ],
            CHANGING_BIO: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, _set_bio_recv),
            ],
            CHANGING_AVATAR: [
                MessageHandler(filters.PHOTO, _set_avatar_recv),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(back_to_menu, pattern="^back_menu$"),
            CallbackQueryHandler(back_to_settings, pattern="^back_settings$"),
        ],
    )
    application.add_handler(settings_conv)

    # Profile / Inventory / Pets (simple callbacks)
    application.add_handler(
        CallbackQueryHandler(profile_menu, pattern="^menu_profile$")
    )
    application.add_handler(
        CallbackQueryHandler(inventory_menu, pattern="^menu_inventory$")
    )
    application.add_handler(
        CallbackQueryHandler(pets_menu, pattern="^menu_pets$")
    )
    application.add_handler(
        CallbackQueryHandler(back_to_menu, pattern="^back_menu$")
    )

    # Admin conversation
    admin_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(admin_menu, pattern="^menu_admin$"),
        ],
        states={
            # 0 is not used here, admin menu is entry point
            # Use arbitrary numbers for states
            CHOOSING_SETTINGS: [  # reuse or not, doesn't matter because entry point different
                CallbackQueryHandler(_admin_stats, pattern="^admin_stats$"),
                CallbackQueryHandler(_admin_hp_start, pattern="^admin_hp$"),
                CallbackQueryHandler(_admin_xp_start, pattern="^admin_xp$"),
                CallbackQueryHandler(_admin_give_start, pattern="^admin_give$"),
            ],
            ADMIN_EDIT_HP: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, _admin_hp_recv),
            ],
            ADMIN_EDIT_XP: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, _admin_xp_recv),
            ],
            ADMIN_GIVE_ITEM_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, _admin_give_id),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(back_to_menu, pattern="^back_menu$"),
            CallbackQueryHandler(back_to_admin, pattern="^menu_admin$|back_admin"),
        ],
    )
    application.add_handler(admin_conv)

    # Misc callback actions
    application.add_handler(
        CallbackQueryHandler(_set_reset_do, pattern="^confirm_reset$")
    )