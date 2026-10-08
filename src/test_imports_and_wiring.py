from src.main import *
from src.scheduler import *
from src.expeditions import *
from src.combat import *
from src.handlers import *
import importlib


_config = importlib.import_module("src.config")
_config_names = set(dir(_config))
DUEL_TIMEZONE = getattr(_config, "DUEL_TIMEZONE", "Europe/Shanghai")
DICK_STEAL_CHANCE = getattr(_config, "DICK_STEAL_CHANCE", 10)


def test_imports_and_wiring():
    assert callable(cb_menu_main)
    assert callable(help_command)
    assert callable(cmd_fight)
    assert callable(cb_fight_start)
    assert callable(cmd_expedition)
    assert callable(cb_exp_start)
    assert callable(scheduler_wakeup)
    assert callable(check_expired_items)
    assert callable(check_expired_items_in_thread)

    expected_regular = {
        "DUEL_TIMEZONE",
        "DICK_STEAL_CHANCE",
        "scheduler_wakeup",
        "cmd_fight",
        "cb_menu_fight",
        "cb_fight_start",
        "cmd_expedition",
        "cb_menu_expedition",
        "cb_exp_start",
        "cb_exp_finish",
        "cb_menu_main",
        "cb_menu_inventory",
        "cb_use_item",
        "cb_inventory_back",
        "help_command",
        "rating_command",
        "duel_command",
        "pay_command",
        "steal_command",
        "dick_command",
        "raid_command",
        "start_command",
        "stats_command",
        "shop_command",
        "buy_command",
        "chat_gpt_command",
        "pre_checkin_command",
        "checkin_command",
        "admin_stats_command",
        "top_dicks_command",
        "reset_dicks_command",
        "broadcast_command",
    }

    assert DUEL_TIMEZONE == "Europe/Shanghai"
    assert 0 < DICK_STEAL_CHANCE <= 100

    main_names = set(dir())
    REGULAR_NAMES = main_names - {"application", "time", "datetime", "logging"}
    assert REGULAR_NAMES.issuperset(expected_regular), (
        f"main.py is missing expected non-handler names: "
        f"{sorted(expected_regular - REGULAR_NAMES)}.  Did you forget to import?"
    )

    assert callable(check_expired_items)
    assert callable(check_expired_items_in_thread)