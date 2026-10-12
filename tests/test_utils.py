from types import SimpleNamespace
from unittest.mock import AsyncMock

import json

import pytest

from text_resources import get_text, get_text_mapping


def test_media_text_resources_preserve_labels_and_html_placeholder():
    assert get_text_mapping("media.types") == {
        "file": "File",
        "photo": "Photo",
        "gif": "GIF",
        "video": "Video",
        "document": "Document",
        "sticker": "Sticker",
    }
    assert get_text(
        "media.templates.file_id",
        media_type="Photo",
        file_id="photo-id",
    ) == "Photo file_id:\n<code>photo-id</code>"


@pytest.mark.asyncio
async def test_file_id_handler_uses_media_template(monkeypatch):
    from handlers import utils

    reply_text = AsyncMock()
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=1),
        photo=[SimpleNamespace(file_id="photo-id")],
        animation=None,
        video=None,
        document=None,
        sticker=None,
        reply_text=reply_text,
    )
    monkeypatch.setattr(utils, "is_admin", lambda _user_id: True)

    await utils.get_file_id_handler(SimpleNamespace(message=message), None)

    reply_text.assert_awaited_once_with(
        "Photo file_id:\n<code>photo-id</code>",
        parse_mode="HTML",
    )


# ---------------------------------------------------------------------
# Самоочистка сообщений бота
# ---------------------------------------------------------------------

def _scheduled_self_delete(fake_context):
    from handlers import utils

    return [
        call for call in fake_context.job_queue.calls
        if call[0] is utils.self_delete_job
    ]


async def test_self_delete_job_deletes_message(fake_context, monkeypatch):
    from handlers import utils

    fake_context.job = SimpleNamespace(data={"chat_id": 42, "message_id": 101})
    monkeypatch.setattr(utils, "is_auto_delete_enabled", lambda _chat_id: True)

    await utils.self_delete_job(fake_context)

    fake_context.bot.delete_message.assert_awaited_once_with(chat_id=42, message_id=101)


async def test_self_delete_respects_chat_toggle(fake_context, monkeypatch):
    from handlers import utils

    fake_context.job = SimpleNamespace(data={"chat_id": 42, "message_id": 101})
    monkeypatch.setattr(utils, "is_auto_delete_enabled", lambda _chat_id: False)

    await utils.self_delete_job(fake_context)

    fake_context.bot.delete_message.assert_not_awaited()


async def test_stats_reply_schedules_self_delete(fake_context, tmp_path, monkeypatch):
    import handlers.adventure as adventure
    from handlers import utils

    data_path = tmp_path / "player_data.json"
    data_path.write_text(json.dumps({"1001": {"hp": 200, "max_hp": 200, "lvl": 1}}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(adventure, "PLAYER_DATA_PATH", str(data_path))

    reply = AsyncMock(return_value=SimpleNamespace(chat_id=-42, message_id=100))
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1001, username="tester", first_name="Tester"),
        effective_message=SimpleNamespace(chat_id=-42, message_id=99, reply_text=reply),
    )

    await adventure.adventure_stats_command(update, fake_context)

    scheduled = _scheduled_self_delete(fake_context)
    entries = sorted((call[1], call[2]["data"]["message_id"]) for call in scheduled)
    # Минута и на команду игрока, и на ответ бота.
    assert entries == [(60, 99), (60, 100)]


async def test_inventory_reply_schedules_self_delete(fake_context, tmp_path, monkeypatch):
    import handlers.adventure as adventure
    from handlers import utils

    data_path = tmp_path / "player_data.json"
    data_path.write_text(json.dumps({"1001": {"hp": 200, "max_hp": 200, "lvl": 1}}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(adventure, "PLAYER_DATA_PATH", str(data_path))

    reply = AsyncMock(return_value=SimpleNamespace(chat_id=-42, message_id=101))
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1001),
        effective_message=SimpleNamespace(chat_id=-42, message_id=100, reply_text=reply),
    )

    await adventure.adventure_inventory_command(update, fake_context)

    scheduled = _scheduled_self_delete(fake_context)
    entries = sorted((call[1], call[2]["data"]["message_id"]) for call in scheduled)
    assert entries == [(60, 100), (60, 101)]


async def test_inventory_new_renders_fruit_descriptions(fake_context, tmp_path, monkeypatch):
    """Описание фрукта из общего каталога показывается в инвентаре."""
    import handlers.adventure as adventure

    data_path = tmp_path / "player_data.json"
    data_path.write_text(json.dumps({"1001": {
        "hp": 200, "max_hp": 200, "lvl": 1,
        "inventory": [
            {"name": "🍎 Смоляное яблоко", "quantity": 1},
            {"name": "🌿 Хвоя", "quantity": 1},
            {"name": "🌿 Хвоя", "quantity": 1},
            {"name": "🌿 Хвоя", "quantity": 1},
        ],
    }}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(adventure, "PLAYER_DATA_PATH", str(data_path))

    pool_path = tmp_path / "global_pool.json"
    pool_path.write_text(json.dumps({"items": {
        "resin_apple": {"name": "🍎 Смоляное яблоко", "description": "Выросло на смоле Барона. Липкое."},
    }}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(adventure, "GLOBAL_POOL_PATH", str(pool_path))

    reply = AsyncMock(return_value=SimpleNamespace(chat_id=-42, message_id=100))
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1001),
        effective_message=SimpleNamespace(chat_id=-42, message_id=103, reply_text=reply),
    )

    await adventure.adventure_inventory_command(update, fake_context)

    text = reply.await_args.args[0]
    assert "🍎 Смоляное яблоко: 1 шт." in text
    assert "🌿 Хвоя: 3 шт." in text
    assert "Выросло на смоле Барона. Липкое." in text
    # Хвоя — ресурс локации, описаний в каталоге нет: только в одну строку
    assert text.count("Хвоя") == 1


async def test_help_reply_schedules_self_delete(fake_context):
    import bot

    reply = AsyncMock(return_value=SimpleNamespace(chat_id=-42, message_id=102))
    update = SimpleNamespace(
        effective_message=SimpleNamespace(chat_id=-42, message_id=101, reply_text=reply),
    )

    await bot.help_command(update, fake_context)

    scheduled = _scheduled_self_delete(fake_context)
    entries = sorted((call[1], call[2]["data"]["message_id"]) for call in scheduled)
    assert entries == [(60, 101), (60, 102)]
    reply.assert_awaited_once()


async def test_self_delete_survives_missing_rights(fake_context, monkeypatch):
    """Без админ-прав бот не может удалить чужое сообщение — но не падает."""
    from handlers import utils

    fake_context.job = SimpleNamespace(data={"chat_id": 42, "message_id": 101})
    monkeypatch.setattr(utils, "is_auto_delete_enabled", lambda _chat_id: True)
    fake_context.bot.delete_message = AsyncMock(side_effect=Exception("no rights"))

    await utils.self_delete_job(fake_context)

    fake_context.bot.delete_message.assert_awaited_once()
