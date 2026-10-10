import importlib
import logging

from telegram.ext import CallbackQueryHandler, CommandHandler


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
        "help_command",
        "error_handler",
        "adventure_command",
        "adventure_inventory_command",
        "adventure_stats_command",
        "adventure_callback",
        "expedition_expiration_job",
        "main",
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
        ("help", "Справка"),
        ("adventure", "Экспедиции"),
        ("adventure_stats", "Статистика экспедиции"),
        ("adventure_inventory", "Инвентарь экспедиции"),
    ]


def test_main_registers_adventure_handlers(monkeypatch):
    import bot

    registered_handlers = []

    class FakeJobQueue:
        def run_repeating(self, *args, **kwargs):
            return None

    class FakeApplication:
        job_queue = FakeJobQueue()

        def add_handler(self, handler, group=0):
            registered_handlers.append(handler)

        def add_error_handler(self, _handler):
            pass

        def run_polling(self, **_kwargs):
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

    monkeypatch.setattr(bot, "BOT_TOKEN", "test-token")
    monkeypatch.setattr(bot, "init_db", lambda: None)
    monkeypatch.setattr(bot.Application, "builder", lambda: FakeBuilder())

    bot.main()

    command_handlers = [
        item for item in registered_handlers if isinstance(item, CommandHandler)
    ]
    commands = {next(iter(item.commands)) for item in command_handlers}
    assert {"start", "help", "adventure", "adventure_stats", "adventure_inventory"} <= commands
    assert any(isinstance(item, CallbackQueryHandler) for item in registered_handlers)
