import importlib

import pytest


def test_runtime_modules_import():
    for name in (
        "config",
        "database",
        "handlers.adventure",
        "handlers.adventure_expiration_checker",
        "handlers.utils",
        "bot",
    ):
        assert importlib.import_module(name)


def test_bot_public_import_contracts():
    import bot

    names = (
        "init_db",
        "start_command",
        "daily_beauty_job",
        "error_handler",
        "adventure_command",
        "adventure_inventory_command",
        "adventure_stats_command",
        "adventure_callback",
        "adventure_expiration_check_callback",
    )
    for name in names:
        assert callable(getattr(bot, name))


def test_bot_suppresses_http_client_info_logs():
    import bot  # noqa: F401

    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING


def test_bot_command_menu_preserves_descriptions_and_order():
    import bot

    assert [(command.command, command.description) for command in bot.BOT_COMMANDS] == [
        ("start", "Запустить бота"),
        ("help", "Хелп по командам"),
        ("donate", "Поддержать проект"),
        ("adventure", "Экспедиции"),
        ("adventure_inventory", "Инвентарь экспедиции"),
        ("adventure_stats", "Статистика экспедиции"),
    ]


@pytest.mark.asyncio
async def test_donate_command_handler_is_registered_once(monkeypatch):
    from telegram.ext import CommandHandler
    import bot

    registered_handlers = []

    class FakeApplication:
        job_queue = None

        def add_handler(self, handler, group=0):
            registered_handlers.append(handler)

        def add_error_handler(self, _handler):
            pass

        async def run_polling(self, **_kwargs):
            pass

    class FakeBuilder:
        def __init__(self):
            self.application = FakeApplication()

        def token(self, _token):
            return self

        def post_init(self, _callback):
            return self

        def build(self):
            return self.application

    monkeypatch.setattr(bot.nest_asyncio, "apply", lambda: None)
    monkeypatch.setattr(bot, "init_db", lambda: None)
    monkeypatch.setattr(bot.Application, "builder", lambda: FakeBuilder())

    await bot.main()

    handlers = [
        item
        for item in registered_handlers
        if isinstance(item, CommandHandler) and item.callback is bot.donate_command
    ]
    assert len(handlers) == 1
    assert handlers[0].commands == frozenset({"donate"})
