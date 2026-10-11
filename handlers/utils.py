import logging
from telegram import Update
from telegram.ext import ContextTypes
from config import ADMIN_IDS
from database import is_auto_delete_enabled
from text_resources import get_text, get_text_mapping


MEDIA_TYPE_LABELS = get_text_mapping("media.types")


def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором."""
    if ADMIN_IDS and user_id in ADMIN_IDS:
        return True
    return False


async def delete_messages_job(context: ContextTypes.DEFAULT_TYPE):
    """Фоновая задача для автоудаления команд и реакций."""
    data = context.job.data
    chat_id = data.get("chat_id")
    message_id = data.get("message_id")

    # Сначала пытаемся поставить реакцию 👍
    try:
        await context.bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction="👍"
        )
    except Exception as e:
        logging.warning(f"Не удалось поставить реакцию на сообщение {message_id}: {e}")

    # Удаляем сообщение пользователя, если в чате включено автоудаление
    if is_auto_delete_enabled(chat_id):
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception as e:
            logging.warning(f"Не удалось удалить сообщение {message_id}: {e}")


async def reply_or_send(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    parse_mode: str = None,
    **kwargs
):
    """Отправляет ответ на сообщение или просто в чат, если исходное сообщение удалено."""
    try:
        if update.message:
            return await update.message.reply_text(text, parse_mode=parse_mode, **kwargs)
        else:
            return await context.bot.send_message(
                chat_id=update.effective_chat.id, text=text, parse_mode=parse_mode, **kwargs
            )
    except Exception:
        return await context.bot.send_message(
            chat_id=update.effective_chat.id, text=text, parse_mode=parse_mode, **kwargs
        )


async def get_file_id_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Хэндлер для получения file_id медиа (только для администраторов)."""
    msg = update.message
    if not msg or not msg.from_user:
        return
    
    # Проверка на права администратора
    if not is_admin(msg.from_user.id):
        return

    file_id = None
    media_type = MEDIA_TYPE_LABELS["file"]

    # 1. Сжатая картинка (Telegram отправляет массив размеров, берем самое высокое качество - последний элемент)
    if msg.photo:
        file_id = msg.photo[-1].file_id
        media_type = MEDIA_TYPE_LABELS["photo"]

    # 2. Анимация / GIF
    elif msg.animation:
        file_id = msg.animation.file_id
        media_type = MEDIA_TYPE_LABELS["gif"]

    # 3. Видео
    elif msg.video:
        file_id = msg.video.file_id
        media_type = MEDIA_TYPE_LABELS["video"]

    # 4. Документ (картинка или файл, отправленный "без сжатия")
    elif msg.document:
        file_id = msg.document.file_id
        media_type = MEDIA_TYPE_LABELS["document"]

    # 5. Стикер
    elif msg.sticker:
        file_id = msg.sticker.file_id
        media_type = MEDIA_TYPE_LABELS["sticker"]

    if file_id:
        await msg.reply_text(
            get_text("media.templates.file_id", media_type=media_type, file_id=file_id),
            parse_mode="HTML",
        )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Глобальный обработчик ошибок."""
    logging.error("Исключение при обработке запроса:", exc_info=context.error)


# ---------------------------------------------------------------------
# Самоочистка сообщений бота
# ---------------------------------------------------------------------

DEFAULT_DELETE_DELAY = 60


async def self_delete_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """JobQueue callback: удалить запомненное сообщение бота.

    Учитывает пер-чат выключатель is_auto_delete_enabled (legacy-таблица
    chat_settings, по умолчанию включено). Ошибки удаления (сообщение уже
    удалено и т.п.) молча логируются.
    """
    data = context.job.data if context.job else None
    if not data:
        return
    if not is_auto_delete_enabled(data["chat_id"]):
        return
    try:
        await context.bot.delete_message(
            chat_id=data["chat_id"], message_id=data["message_id"]
        )
    except Exception as e:
        logging.warning(
            "Не удалось удалить сообщение %s/%s: %s",
            data["chat_id"], data["message_id"], e,
        )


async def schedule_self_delete(
    context: ContextTypes.DEFAULT_TYPE,
    message,
    delay_seconds: int = DEFAULT_DELETE_DELAY,
) -> None:
    """Запланировать удаление сообщения через delay секунд.

    Подходит и для сообщений бота, и для команд игрока (для чужих сообщений
    боту нужны права админа: без них удаление просто не сработает, error
    пойдёт warning'ом и бот продолжит работу).
    """
    if message is None or not context.job_queue:
        return
    context.job_queue.run_once(
        self_delete_job,
        delay_seconds,
        name=f"self_delete_{message.chat_id}_{message.message_id}",
        data={"chat_id": message.chat_id, "message_id": message.message_id},
    )
