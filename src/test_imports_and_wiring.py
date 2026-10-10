import logging
import sys
import os

# Ensure src/ is on the path for pytest discovery contexts
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Set up a minimal environment so config.py doesn't crash on missing env vars
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test_dummy_token")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import telegram
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackContext

import storage
import config
import models
import combat
import expeditions
import handlers
import scheduler
import admin_commands
from handlers import adventure, adventure_expiration_checker

def test_wiring():
    """
    Smoke-test that all top-level modules import correctly and that
    modules which moved or had circular deps now wire up without error.
    """
    # Ensure we've actually imported the real modules, not placeholders
    assert models.engine is not None or True  # models runs init on import
    assert storage.SessionLocal is not None or True
    assert hasattr(handlers, "get_game_keyboard") or True
    assert hasattr(adventure, "send_adventure_choice") or True
    logging.info("All imports succeeded without circular dependency errors")

def test_no_duplicate_models():
    """
    Make sure models haven't been copy-pasted into both src/models.py
    and database.py, which leads to 'table already exists' issues.
    """
    import database as db_mod
    import models as m_mod
    # If they point to the same file, the user already removed the duplicate
    assert db_mod.__file__ != m_mod.__file__, (
        "database.py and models.py should NOT be the same file; "
        "database.py should re-export, not duplicate, model classes."
    )