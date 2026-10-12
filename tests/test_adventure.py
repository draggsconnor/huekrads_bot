import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta, timezone

import pytest

import handlers.adventure as adv


@pytest.fixture
def mock_player_basic():
    return {
        "user_id": 123,
        "username": "testuser",
        "hp": 200,
        "max_hp": 200,
        "lvl": 1,
        "xp": 0,
        "gold": 0,
        "inventory": [],
        "last_fight_time": None,
        "total_fights": 0,
        "wins": 0,
        "active_expedition": None,
    }


@pytest.fixture
def mock_locations():
    return {
        "dark_forest": {
            "name": "🌲Охуенно тёмный лес",
            "description": "Лес с особыми соснами",
            "duration": 300,
            "required_level": 1,
            "encounter_chance": 0.5,
            "loot_table": [
                {"item_id": "pine_needles", "name": "🌿 Хвоя", "chance": 0.6, "quantity_min": 1, "quantity_max": 3},
                {"item_id": "wood", "name": "🪵 Древесина", "chance": 0.45, "quantity_min": 1, "quantity_max": 2},
                {"item_id": "pine_cone", "name": "🌰 Шишка", "chance": 0.35, "quantity_min": 1, "quantity_max": 1},
            ]
        },
        "abandoned_mine": {
            "name": "Заброшенная шахта",
            "description": "Старая шахта",
            "duration": 300,
            "required_level": 3,
            "encounter_chance": 0.5,
            "loot_table": []
        },
        "mountain_lair": {
            "name": "Логово горного уебана",
            "description": "Логово босса",
            "duration": 300,
            "required_level": 5,
            "encounter_chance": 0.5,
            "loot_table": []
        }
    }


@pytest.fixture
def mock_mobs():
    return {
        "specific_mobs": {
            "dark_forest": [
                {
                    "name": "🌳Куст с предъявами",
                    "hp": 30,
                    "attack": 5,
                    "defense": 1,
                    "speed": 3,
                    "crit_chance": 0.0,
                    "encounter_chance": 35,
                    "min_level": 1,
                    "tactics": {"debuff_chance": 0.3, "debuff_name": "Паранойя"},
                },
                {
                    "name": "Голова деда на паучьих ногах",
                    "hp": 20,
                    "attack": 8,
                    "defense": 0,
                    "speed": 5,
                    "crit_chance": 0.1,
                    "encounter_chance": 35,
                    "min_level": 1,
                    "tactics": {"overpower": True, "overpower_threshold": 3},
                },
            ]
        }
    }



class TestFormatResource:
    """Тесты форматирования ресурсов"""

    def test_format_resource_with_quantity(self):
        resource = {"name": "Хвоя", "quantity": 3}
        result = adv._format_resource(resource)
        assert "Хвоя" in result
        assert "x3" in result

    def test_format_resource_without_name(self):
        resource = {"item_id": "unknown_item", "quantity": 1}
        result = adv._format_resource(resource)
        assert "unknown_item" in result


class TestPlayerData:
    """Тесты работы с данными игрока"""

    def test_get_player_initializes_new_player(self, tmp_path):
        """Тест инициализации нового игрока"""
        with patch.object(adv, 'PLAYER_DATA_PATH', str(tmp_path / "player_data.json")):
            player = adv.get_player(999)
            
            assert player["hp"] == 200
            assert player["max_hp"] == 200
            assert player["lvl"] == 1
            assert player["inventory"] == []
            assert player["active_expedition"] is None

    def test_get_player_returns_existing_player(self, tmp_path):
        """Тест получения существующего игрока"""
        import json
        player_data = {
            "999": {"hp": 150, "max_hp": 200, "lvl": 5, "inventory": [], "active_expedition": None}
        }
        data_path = tmp_path / "player_data.json"
        with open(data_path, 'w', encoding='utf-8') as f:
            json.dump(player_data, f)
        
        with patch.object(adv, 'PLAYER_DATA_PATH', str(data_path)):
            player = adv.get_player(999)
            
            assert player["hp"] == 150
            assert player["lvl"] == 5

    def test_update_player_updates_fields(self, tmp_path):
        """Тест обновления данных игрока"""
        import json
        player_data = {"999": {"hp": 200, "xp": 0, "inventory": [], "active_expedition": None}}
        data_path = tmp_path / "player_data.json"
        with open(data_path, 'w', encoding='utf-8') as f:
            json.dump(player_data, f)
        
        with patch.object(adv, 'PLAYER_DATA_PATH', str(data_path)):
            adv.update_player(999, hp=180, xp=100)
            
            # Перезагрузка чтобы проверить изменения
            with open(data_path, 'r', encoding='utf-8') as f:
                updated = json.load(f)
            
            assert updated["999"]["hp"] == 180
            assert updated["999"]["xp"] == 100


class TestLoaders:
    """Тесты загрузчиков данных"""

    def test_load_mobs(self, mock_mobs, tmp_path):
        """Тест загрузки данных мобов"""
        import json
        mobs_path = tmp_path / "mobs.json"
        with open(mobs_path, 'w', encoding='utf-8') as f:
            json.dump(mock_mobs, f)
        
        with patch.object(adv, 'MOBS_PATH', str(mobs_path)):
            mobs = adv.load_mobs()
            assert "specific_mobs" in mobs
            assert "dark_forest" in mobs["specific_mobs"]


def _write_json(path, data):
    import json
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _make_expedition(loc_id="test_loc", expired=True):
    from datetime import datetime, timedelta, timezone
    start = datetime.now(timezone.utc) - timedelta(minutes=10)
    end = start + timedelta(minutes=5)
    if not expired:
        end = datetime.now(timezone.utc) + timedelta(minutes=5)
    return {
        "location_id": loc_id,
        "location_name": "Тестовая локация",
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "chat_id": 42,
        "username": "tester",
    }


EMPTY_MOBS = {}
EMPTY_LOCATIONS = {
    "test_loc": {
        "id": "test_loc",
        "name": "Тестовая локация",
        "resources": [],
        "bosses": [],
        "requirements": {"min_level": 1},
    }
}


class TestRollResources:
    def test_multi_drop_eggs(self, tmp_path, monkeypatch):
        from tests.test_battle import FakeRandom

        # random()=0.0 -> бросок успешен; randint(min,max)=min -> 2 яйца.
        monkeypatch.setattr(adv, "random", FakeRandom(0.0))
        loc = {"resources": [{
            "id": "pet_egg", "name": "🥚 Яйцо питомца",
            "chance": 1.0, "quantity_min": 2, "quantity_max": 3,
        }]}
        resources = adv._roll_resources(loc)
        assert [r["name"] for r in resources] == ["🥚 Яйцо питомца", "🥚 Яйцо питомца"]
        assert all(r["quantity"] == 1 for r in resources)

    def test_default_single(self):
        loc = {"resources": [{"id": "res", "name": "🌿 Хвоя", "chance": 1.0}]}
        assert adv._roll_resources(loc) == [{"name": "🌿 Хвоя", "quantity": 1}]


class TestAdventureStatsXpLeft:
    async def test_stats_show_xp_left(self, tmp_path, monkeypatch, fake_context):
        data_path = tmp_path / "player_data.json"
        with open(data_path, "w", encoding="utf-8") as f:
            json.dump({"1001": {"hp": 200, "max_hp": 200, "lvl": 1, "xp": 40}}, f, ensure_ascii=False)
        monkeypatch.setattr(adv, "PLAYER_DATA_PATH", str(data_path))

        reply = AsyncMock(return_value=SimpleNamespace(chat_id=-42, message_id=500))
        await adv.adventure_stats_command(
            SimpleNamespace(
                effective_user=SimpleNamespace(id=1001, username="tester", first_name="Tester"),
                effective_message=SimpleNamespace(chat_id=-42, message_id=501, reply_text=reply),
            ),
            fake_context,
        )

        text = reply.await_args.args[0]
        assert "⭐ Опыт: 40" in text
        # Порог левелапа — фиксированные 100 XP: до ур. 2 осталось 60.
        assert "⬆️ До левелапа (ур. 2): 60 XP" in text


class TestLeadingEmoji:
    def test_does_not_duplicate_first_letter(self):
        # было: '🌲Охуенно тёмный лес'[:2] -> '🌲О' + 'Охуенно…' -> 'ООхуенно'
        assert adv._leading_emoji("🌲Охуенно тёмный лес") == "🌲"

    def test_keeps_variation_selector(self):
        assert adv._leading_emoji("⛏️Заброшенная шахта") == "⛏️"

    def test_fallback_for_missing_display_name(self):
        assert adv._leading_emoji("") == "🌲"


class TestEatInventory:
    """Ядро поедания фруктов (adventure.eat_inventory_item)"""

    EAT_POOL = {
        "mandarin": {"name": "🍊 Мандарин Храбрости", "heal_hp": 40},
        "rotten_melon": {"name": "🍈 Гнилая дыня", "heal_hp": 100},
        "herb": {"name": "🌿 Трава"},  # несъедобно: нет heal_hp
    }

    def _setup(self, tmp_path, monkeypatch, player):
        data_path = tmp_path / "player_data.json"
        with open(data_path, "w", encoding="utf-8") as f:
            json.dump({"123": player}, f, ensure_ascii=False)
        monkeypatch.setattr(adv, "PLAYER_DATA_PATH", str(data_path))

        pool_path = tmp_path / "global_pool.json"
        with open(pool_path, "w", encoding="utf-8") as f:
            json.dump({"items": self.EAT_POOL}, f, ensure_ascii=False)
        monkeypatch.setattr(adv, "GLOBAL_POOL_PATH", str(pool_path))
        return data_path

    def _read(self, data_path):
        with open(data_path, encoding="utf-8") as f:
            return json.load(f)["123"]

    def test_eat_heals_and_caps(self, tmp_path, monkeypatch):
        data_path = self._setup(tmp_path, monkeypatch, {
            "hp": 100, "max_hp": 200,
            "inventory": [
                {"name": "🍊 Мандарин Храбрости", "quantity": 1},
                {"name": "🍊 Мандарин Храбрости", "quantity": 1},
                {"name": "🍊 Мандарин Храбрости", "quantity": 1},
                {"name": "🌿 Трава", "quantity": 1},
            ],
        })
        mandarin = adv._item_hash("🍊 Мандарин Храбрости")

        r1 = adv.eat_inventory_item(123, mandarin)
        assert r1["event"] == "eaten"
        assert r1["healed"] == 40
        assert r1["current"] == 140

        r2 = adv.eat_inventory_item(123, mandarin)
        assert r2["current"] == 180

        # Третий доходит только да капа: 180 + 20 из 40.
        r3 = adv.eat_inventory_item(123, mandarin)
        assert r3["healed"] == 20
        assert r3["current"] == 200

        stored = self._read(data_path)
        assert len([i for i in stored["inventory"] if "Мандарин" in i["name"]]) == 0
        assert stored["hp"] == 200

        # Всё съедено — нельзя.
        r4 = adv.eat_inventory_item(123, mandarin)
        assert r4["event"] == "empty"

    def test_eat_blocked_during_battle(self, tmp_path, monkeypatch):
        data_path = self._setup(tmp_path, monkeypatch, {
            "hp": 100, "max_hp": 200,
            "active_battle": {"battle_id": "abc"},
            "inventory": [{"name": "🍈 Гнилая дыня", "quantity": 1}],
        })
        melon = adv._item_hash("🍈 Гнилая дыня")

        result = adv.eat_inventory_item(123, melon)
        assert result == {"event": "battle"}
        assert self._read(data_path)["inventory"]  # дыня на месте

    def test_eat_rejects_non_edible_and_wrong_hash(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, {
            "hp": 100, "max_hp": 200,
            "inventory": [{"name": "🌿 Трава", "quantity": 1}],
        })
        herba = adv._item_hash("🌿 Трава")
        assert adv.eat_inventory_item(123, herba)["event"] == "empty"

        garbage = adv.eat_inventory_item(123, "00000000")
        assert garbage["event"] == "empty"


class TestInventoryEatFlow:
    """Кнопки использования в инвентаре (invt_*)"""

    async def test_use_and_eat_flow(self, tmp_path, monkeypatch, fake_context):
        from handlers import utils
        import handlers.adventure as adventure

        data_path = tmp_path / "player_data.json"
        data_path.write_text(json.dumps({"1001": {
            "hp": 100, "max_hp": 200, "lvl": 1,
            "inventory": [
                {"name": "🍊 Мандарин Храбрости", "quantity": 1},
                {"name": "🍊 Мандарин Храбрости", "quantity": 1},
            ],
        }}, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(adventure, "PLAYER_DATA_PATH", str(data_path))

        pool_path = tmp_path / "global_pool.json"
        pool_path.write_text(json.dumps({"items": {
            "mandarin": {"name": "🍊 Мандарин Храбрости", "heal_hp": 40},
        }}, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(adventure, "GLOBAL_POOL_PATH", str(pool_path))

        # 1) /adventure_inventory: у ответа есть кнопка ИСПОЛЬЗОВАТЬ.
        reply = AsyncMock(return_value=SimpleNamespace(chat_id=-42, message_id=200))
        command_update = SimpleNamespace(
            effective_user=SimpleNamespace(id=1001),
            effective_message=SimpleNamespace(chat_id=-42, message_id=199, reply_text=reply),
        )
        await adventure.adventure_inventory_command(command_update, fake_context)

        markup = reply.await_args.kwargs["reply_markup"]
        texts = [b.text for row in markup.inline_keyboard for b in row]
        assert any("ИСПОЛЬЗОВАТЬ" in t for t in texts)

        # 2) ИСПОЛЬЗОВАТЬ: пикер + перенос очистки на 5 минут.
        query = SimpleNamespace(
            data="invt_1001_use", answer=AsyncMock(), edit_message_text=AsyncMock(),
            message=SimpleNamespace(chat_id=-42, message_id=200),
        )
        await adventure.inventory_callback(
            SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=1001)),
            fake_context,
        )
        panel = query.edit_message_text.await_args.kwargs
        assert "Чего слопаем" in panel["text"]
        panel_texts = [b.text for row in panel["reply_markup"].inline_keyboard for b in row]
        assert any("x2 (+40 HP)" in t for t in panel_texts)
        assert any("Отмена" in t for t in panel_texts)
        assert any(
            call[0] is utils.self_delete_job and call[1] == 300
            for call in fake_context.job_queue.calls
        )

    async def test_eat_updates_picker_and_player(self, tmp_path, monkeypatch, fake_context):
        import handlers.adventure as adventure

        data_path = tmp_path / "player_data.json"
        data_path.write_text(json.dumps({"1001": {
            "hp": 100, "max_hp": 200, "lvl": 1,
            "inventory": [{"name": "🍊 Мандарин Храбрости", "quantity": 1}],
        }}, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(adventure, "PLAYER_DATA_PATH", str(data_path))

        pool_path = tmp_path / "global_pool.json"
        pool_path.write_text(json.dumps({"items": {
            "mandarin": {"name": "🍊 Мандарин Храбрости", "heal_hp": 40},
        }}, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(adventure, "GLOBAL_POOL_PATH", str(pool_path))

        mandarin = adventure._item_hash("🍊 Мандарин Храбрости")
        query = SimpleNamespace(
            data=f"invt_1001_eat_{mandarin}", answer=AsyncMock(), edit_message_text=AsyncMock(),
            message=SimpleNamespace(chat_id=-42, message_id=200),
        )
        await adventure.inventory_callback(
            SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=1001)),
            fake_context,
        )

        shown = query.edit_message_text.await_args.kwargs["text"]
        assert "+40 HP" in shown and "140/200" in shown
        # Съел последний фрукт — пикер свернулся, показан обычный инвентарь.
        assert "Чего слопаем" not in shown
        assert "Пусто..." in shown
        assert query.edit_message_text.await_args.kwargs["reply_markup"] is None

        stored = json.load(open(data_path, encoding="utf-8"))["1001"]
        assert stored["hp"] == 140
        assert stored["inventory"] == []

    async def test_not_your_inventory(self, tmp_path, monkeypatch, fake_context):
        import handlers.adventure as adventure

        query = SimpleNamespace(data="invt_999_use", answer=AsyncMock(), edit_message_text=AsyncMock())
        await adventure.inventory_callback(
            SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=1001)),
            fake_context,
        )
        query.answer.assert_awaited_once_with("Это не твой инвентарь.")
        query.edit_message_text.assert_not_awaited()

    async def test_cancel_back_to_inventory(self, tmp_path, monkeypatch, fake_context):
        from handlers import utils
        import handlers.adventure as adventure

        data_path = tmp_path / "player_data.json"
        data_path.write_text(json.dumps({"1001": {
            "hp": 100, "max_hp": 200, "lvl": 1,
            "inventory": [{"name": "🍊 Мандарин Храбрости", "quantity": 1}],
        }}, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(adventure, "PLAYER_DATA_PATH", str(data_path))

        pool_path = tmp_path / "global_pool.json"
        pool_path.write_text(json.dumps({"items": {
            "mandarin": {"name": "🍊 Мандарин Храбрости", "heal_hp": 40},
        }}, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(adventure, "GLOBAL_POOL_PATH", str(pool_path))

        query = SimpleNamespace(
            data="invt_1001_cancel", answer=AsyncMock(), edit_message_text=AsyncMock(),
            message=SimpleNamespace(chat_id=-42, message_id=200),
        )
        await adventure.inventory_callback(
            SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=1001)),
            fake_context,
        )

        text = query.edit_message_text.await_args.kwargs["text"]
        assert "Инвентарь экспедиций" in text
        assert any(
            call[0] is utils.self_delete_job and call[1] == 60
            for call in fake_context.job_queue.calls
        )


class TestClaimActiveExpedition:
    """Тесты атомарного захвата экспедиции"""

    def test_claim_returns_expedition_and_clears_store(self, tmp_path):
        data_path = tmp_path / "player_data.json"
        expedition = _make_expedition()
        _write_json(data_path, {"123": {"hp": 200, "active_expedition": expedition}})

        with patch.object(adv, "PLAYER_DATA_PATH", str(data_path)):
            claimed = adv.claim_active_expedition(123)

        assert claimed == expedition

        import json as json_mod
        with open(data_path, encoding="utf-8") as f:
            stored = json_mod.load(f)
        assert stored["123"]["active_expedition"] is None

    def test_claim_twice_returns_none_second_time(self, tmp_path):
        data_path = tmp_path / "player_data.json"
        _write_json(data_path, {"123": {"hp": 200, "active_expedition": _make_expedition()}})

        with patch.object(adv, "PLAYER_DATA_PATH", str(data_path)):
            assert adv.claim_active_expedition(123) is not None
            assert adv.claim_active_expedition(123) is None

    def test_claim_without_player_record_returns_none(self, tmp_path):
        data_path = tmp_path / "player_data.json"
        _write_json(data_path, {})

        with patch.object(adv, "PLAYER_DATA_PATH", str(data_path)):
            assert adv.claim_active_expedition(123) is None


class TestFinishExpedition:
    """Тесты расчёта итогов экспедиции"""

    def _setup_paths(self, tmp_path, monkeypatch, player, locations, mobs):
        data_path = tmp_path / "player_data.json"
        _write_json(data_path, {"123": player})
        monkeypatch.setattr(adv, "PLAYER_DATA_PATH", str(data_path))

        loc_path = tmp_path / "locations.json"
        _write_json(loc_path, locations)
        monkeypatch.setattr(adv, "LOCATIONS_PATH", str(loc_path))

        mobs_path = tmp_path / "mobs.json"
        _write_json(mobs_path, mobs)
        monkeypatch.setattr(adv, "MOBS_PATH", str(mobs_path))

        return data_path

    def _read_player(self, data_path):
        import json as json_mod
        with open(data_path, encoding="utf-8") as f:
            return json_mod.load(f)["123"]

    def test_finish_without_active_expedition_returns_none(self, tmp_path, monkeypatch):
        data_path = self._setup_paths(
            tmp_path, monkeypatch,
            {"hp": 200, "max_hp": 200, "active_expedition": None},
            EMPTY_LOCATIONS, EMPTY_MOBS,
        )
        assert adv.finish_expedition(123) is None
        assert self._read_player(data_path)["active_expedition"] is None

    def test_finish_completes_expedition_once(self, tmp_path, monkeypatch):
        player = {
            "hp": 200, "max_hp": 200, "lvl": 1, "xp": 0,
            "inventory": [], "active_expedition": _make_expedition(),
        }
        data_path = self._setup_paths(tmp_path, monkeypatch, player, EMPTY_LOCATIONS, EMPTY_MOBS)

        result = adv.finish_expedition(123)
        assert result is not None
        assert result["loc_id"] == "test_loc"
        assert result["username"] == "tester"
        assert result["chat_id"] == 42
        assert "Итоги экспедиции" in result["text"]
        # Без боёв в журнале — тихий путь.
        assert "Путь был тихим" in result["text"]

        stored = self._read_player(data_path)
        assert stored["active_expedition"] is None
        assert stored["total_expeditions"] == 1
        assert stored["total_fights"] == 0

        # Повторный вызов не даёт наград дважды.
        assert adv.finish_expedition(123) is None
        assert self._read_player(data_path)["total_expeditions"] == 1

    def test_finish_renders_battle_log(self, tmp_path, monkeypatch):
        expedition = _make_expedition()
        expedition["battle_log"] = [
            {
                "enemy": "Древесный слизень",
                "result": "victory",
                "rounds": 3,
                "xp": 10,
                "item": "🪨 Камень",
            }
        ]
        player = {
            "hp": 200, "max_hp": 200, "lvl": 1, "xp": 10,
            "inventory": [], "active_expedition": expedition,
        }
        data_path = self._setup_paths(tmp_path, monkeypatch, player, EMPTY_LOCATIONS, EMPTY_MOBS)

        result = adv.finish_expedition(123)
        assert "Древесный слизень повержен за 3 раунда" in result["text"]
        assert "+10 XP" in result["text"]

    def test_finish_levels_up_player(self, tmp_path, monkeypatch):
        player = {
            "hp": 200, "max_hp": 200, "lvl": 1, "xp": 130,
            "inventory": [], "active_expedition": _make_expedition(),
        }
        data_path = self._setup_paths(tmp_path, monkeypatch, player, EMPTY_LOCATIONS, EMPTY_MOBS)

        result = adv.finish_expedition(123)
        assert "НОВЫЙ УРОВЕНЬ" in result["text"]

        stored = self._read_player(data_path)
        # 130 XP: порог 100 -> уровень 2, остаток 30
        assert stored["lvl"] == 2
        assert stored["xp"] == 30
        assert stored["max_hp"] == 220
