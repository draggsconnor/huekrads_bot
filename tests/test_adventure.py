import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

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
