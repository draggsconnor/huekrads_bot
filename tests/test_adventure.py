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


class TestExpeditionResultCleanup:
    async def test_result_message_deleted_after_2min(self, tmp_path, monkeypatch, fake_context):
        """Итоговый отчёт экспедиции живёт 120 секунд (не минуту)."""
        from handlers import utils

        data_path = tmp_path / "player_data.json"
        with open(data_path, "w", encoding="utf-8") as f:
            json.dump({"123": {
                "hp": 200, "max_hp": 200, "lvl": 1, "xp": 0,
                "active_expedition": {
                    "location_id": "dark_forest",
                    "username": "Tester",
                    "chat_id": -42,
                    "battle_log": [],
                },
            }}, f, ensure_ascii=False)
        monkeypatch.setattr(adv, "PLAYER_DATA_PATH", str(data_path))
        fake_context.bot.send_message = AsyncMock(
            return_value=SimpleNamespace(chat_id=-42, message_id=101),
        )

        await adv.send_expedition_result(123, None, "Tester", fake_context)

        scheduled = [
            call for call in fake_context.job_queue.calls
            if call[0] is utils.self_delete_job
        ]
        entries = sorted((call[1], call[2]["data"]["message_id"]) for call in scheduled)
        assert entries == [(120, 101)]


class TestAdventureCommandCleanup:
    async def test_command_and_menu_deleted_after_2min(self, fake_context):
        """/adventure: команда игрока и меню локаций живут 120 секунд."""
        from handlers import utils

        reply = AsyncMock(return_value=SimpleNamespace(chat_id=-42, message_id=600))
        await adv.adventure_command(
            SimpleNamespace(
                effective_user=SimpleNamespace(id=123),
                effective_message=SimpleNamespace(chat_id=-42, message_id=601, reply_text=reply),
            ),
            fake_context,
        )
        scheduled = [c for c in fake_context.job_queue.calls if c[0] is utils.self_delete_job]
        entries = sorted((c[1], c[2]["data"]["message_id"]) for c in scheduled)
        assert entries == [(120, 600), (120, 601)]


class TestLockedLocationBackButton:
    async def test_low_level_screen_has_back_button(self, tmp_path, monkeypatch, fake_context):
        """Экран «Требуется уровень N» не тупик: есть кнопка назад к локациям."""
        data_path = tmp_path / "player_data.json"
        with open(data_path, "w", encoding="utf-8") as f:
            json.dump({"123": {"hp": 200, "max_hp": 200, "lvl": 1}}, f, ensure_ascii=False)
        monkeypatch.setattr(adv, "PLAYER_DATA_PATH", str(data_path))

        query = SimpleNamespace(
            data="adv_loc_abandoned_mine",  # в прод-данных требует уровень 2
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        await adv.adventure_callback(
            SimpleNamespace(
                callback_query=query,
                effective_user=SimpleNamespace(id=123),
            ),
            fake_context,
        )

        args, kwargs = query.edit_message_text.await_args
        assert "Требуется уровень 2" in args[0]
        button = kwargs["reply_markup"].inline_keyboard[0][0]
        assert button.callback_data == "adv_back"


class TestGnomeNaming:
    """/adventure_name: имя гнома заводится один раз, живёт в отображении."""

    def test_set_gnome_name_cas_once(self, tmp_path, monkeypatch):
        data_path = tmp_path / "player_data.json"
        with open(data_path, "w", encoding="utf-8") as f:
            json.dump({"1001": {"hp": 200, "max_hp": 200, "lvl": 1}}, f, ensure_ascii=False)
        monkeypatch.setattr(adv, "PLAYER_DATA_PATH", str(data_path))

        result = adv.set_gnome_name(1001, " Когли Изобретательный ")
        assert result["event"] == "gnome_named"
        assert result["name"] == "Когли Изобретательный"

        # Повтор — уже нельзя никак.
        again = adv.set_gnome_name(1001, "Гном Второй")
        assert again["event"] == "gnome_already_named"

        stored = json.load(open(data_path, encoding="utf-8"))["1001"]
        assert stored["display_name"] == "Когли Изобретательный"
        assert stored["display_named"] is True

    def test_set_gnome_name_escapes_html(self, tmp_path, monkeypatch):
        """Имя хранится HTML-escaped — рендеры не падают (регресс 4503395)."""
        data_path = tmp_path / "player_data.json"
        with open(data_path, "w", encoding="utf-8") as f:
            json.dump({"1001": {"hp": 200, "max_hp": 200, "lvl": 1}}, f, ensure_ascii=False)
        monkeypatch.setattr(adv, "PLAYER_DATA_PATH", str(data_path))

        result = adv.set_gnome_name(1001, "<имя>&")
        assert result["event"] == "gnome_named"
        assert result["name"] == "&lt;имя&gt;&amp;"

    def test_empty_name_rejected(self, tmp_path, monkeypatch):
        data_path = tmp_path / "player_data.json"
        with open(data_path, "w", encoding="utf-8") as f:
            json.dump({"1001": {"hp": 200, "max_hp": 200, "lvl": 1}}, f, ensure_ascii=False)
        monkeypatch.setattr(adv, "PLAYER_DATA_PATH", str(data_path))

        result = adv.set_gnome_name(1001, "   ")
        assert result["event"] == "empty_name"
        stored = json.load(open(data_path, encoding="utf-8"))["1001"]
        assert stored.get("display_named", False) is False

    def test_display_username_named_and_unnamed(self):
        named = {"display_name": "Когли Изобретательный", "display_named": True}
        assert adv.display_username(named, "draggsconnor") == "Когли Изобретательный (draggsconnor)"
        assert adv.display_username({}, "draggsconnor") == "draggsconnor"
        assert adv.display_username({"display_named": True, "display_name": ""}, "t") == "t"

    async def test_stats_show_display_name(self, tmp_path, monkeypatch, fake_context):
        data_path = tmp_path / "player_data.json"
        with open(data_path, "w", encoding="utf-8") as f:
            json.dump({"1001": {
                "hp": 200, "max_hp": 200, "lvl": 1, "xp": 0,
                "display_name": "Когли Изобретательный", "display_named": True,
            }}, f, ensure_ascii=False)
        monkeypatch.setattr(adv, "PLAYER_DATA_PATH", str(data_path))

        reply = AsyncMock(return_value=SimpleNamespace(chat_id=-42, message_id=500))
        await adv.adventure_stats_command(
            SimpleNamespace(
                effective_user=SimpleNamespace(id=1001, username="draggsconnor", first_name="D"),
                effective_message=SimpleNamespace(chat_id=-42, message_id=501, reply_text=reply),
            ),
            fake_context,
        )
        text = reply.await_args.args[0]
        assert "👤 <b>Когли Изобретательный (draggsconnor)</b>" in text

    async def test_battle_message_uses_display_name(self, tmp_path, monkeypatch):
        """Бой и отчёты: «Имя (username)» вместо голого юзернейма."""
        import handlers.battle as battle

        data_path = tmp_path / "player_data.json"
        with open(data_path, "w", encoding="utf-8") as f:
            json.dump({"1001": {
                "hp": 200, "max_hp": 200, "lvl": 1, "xp": 0, "inventory": [],
                "display_name": "Когли Изобретательный", "display_named": True,
                "active_expedition": {
                    "location_id": "test_loc",
                    "location_name": "Тестовая локация",
                    "start_time": (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
                    "end_time": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
                    "duration_seconds": 900,
                    "chat_id": 42,
                    "username": "draggsconnor",
                    "encounter_scheduled": True,
                    "encounter_at": (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat(),
                },
            }}, f, ensure_ascii=False)
        monkeypatch.setattr(adv, "PLAYER_DATA_PATH", str(data_path))

        state = battle.try_start_battle(1001)
        assert state is not None
        assert state["display"] == "Когли Изобретательный (draggsconnor)"
        text = battle._battle_message_text(state)
        assert "Когли Изобретательный (draggsconnor) против" in text

    async def test_name_flow_command_button_then_text(
        self, tmp_path, monkeypatch, fake_context,
    ):
        """/adventure_name → кнопка (ставит ожидание) → текст именует гнома."""
        data_path = tmp_path / "player_data.json"
        with open(data_path, "w", encoding="utf-8") as f:
            json.dump({"1001": {"hp": 200, "max_hp": 200, "lvl": 1}}, f, ensure_ascii=False)
        monkeypatch.setattr(adv, "PLAYER_DATA_PATH", str(data_path))

        # Команда: интро + кнопка «Я ХОРОШО ПОДУМАЛ».
        reply = AsyncMock(return_value=SimpleNamespace(chat_id=-42, message_id=600))
        await adv.adventure_name_command(
            SimpleNamespace(
                effective_user=SimpleNamespace(id=1001, username="draggsconnor"),
                effective_message=SimpleNamespace(chat_id=-42, message_id=601, reply_text=reply),
            ),
            fake_context,
        )
        assert "Я ХОРОШО ПОДУМАЛ" in str(reply.await_args.kwargs["reply_markup"])
        assert "один раз" in reply.await_args.args[0]

        # Кнопка: тумблер ожидания (scoped на чат).
        btn = reply.await_args.kwargs["reply_markup"].inline_keyboard[0][0]
        query = SimpleNamespace(
            data=btn.callback_data, answer=AsyncMock(),
            message=SimpleNamespace(chat_id=-42, message_id=600),
        )
        await adv.gnome_name_callback(
            SimpleNamespace(
                callback_query=query,
                effective_user=SimpleNamespace(id=1001),
            ),
            fake_context,
        )
        assert fake_context.user_data[adv.GNOME_PENDING_KEY] == -42

        # Текст-ввод: имя гнома фиксируется (обработчик диспетчеризуется из pets).
        import handlers.pets as pets
        name_reply = AsyncMock(return_value=SimpleNamespace(chat_id=-42, message_id=700))
        message = SimpleNamespace(
            text="Когли Изобретательный", chat_id=-42, message_id=701,
            reply_text=name_reply,
        )
        await pets.pet_name_text_handler(
            SimpleNamespace(
                effective_user=SimpleNamespace(id=1001),
                effective_message=message,
            ),
            fake_context,
        )
        assert "Отныне гнома зовут <b>Когли Изобретательный</b>" in name_reply.await_args.args[0]
        stored = json.load(open(data_path, encoding="utf-8"))["1001"]
        assert stored["display_named"] is True

        # Повторная команда — «имя уже дано», кнопки больше нет.
        second = AsyncMock(return_value=SimpleNamespace(chat_id=-42, message_id=800))
        await adv.adventure_name_command(
            SimpleNamespace(
                effective_user=SimpleNamespace(id=1001, username="draggsconnor"),
                effective_message=SimpleNamespace(chat_id=-42, message_id=801, reply_text=second),
            ),
            fake_context,
        )
        assert "Имя уже дано" in second.await_args.args[0]
        assert "reply_markup" not in second.await_args.kwargs


class TestAdminGnomeName:
    """Админские /admin_gnome_name и /admin_gnome_reset — CAS в обход."""

    def _player_file(self, tmp_path):
        data_path = tmp_path / "player_data.json"
        with open(data_path, "w", encoding="utf-8") as f:
            json.dump({"1001": {"hp": 200, "max_hp": 200, "lvl": 1,
                                "display_name": "Когди Изобретательный",
                                "display_named": True}}, f, ensure_ascii=False)
        return data_path

    async def _run(self, monkeypatch, fake_context, data_path, text, is_admin=True):
        monkeypatch.setattr(adv, "PLAYER_DATA_PATH", str(data_path))
        monkeypatch.setattr(adv, "is_admin", lambda uid: is_admin)
        reply = AsyncMock(return_value=SimpleNamespace(chat_id=-42, message_id=900))
        await adv.admin_gnome_command(
            SimpleNamespace(
                effective_user=SimpleNamespace(id=999),
                effective_message=SimpleNamespace(chat_id=-42, message_id=901, text=text, reply_text=reply),
            ),
            SimpleNamespace(args=text.split()[1:], user_data={}, **{"job_queue": None, "bot": fake_context.bot}),
        )
        return reply

    async def test_non_admin_denied(self, tmp_path, monkeypatch, fake_context):
        data_path = self._player_file(tmp_path)
        reply = await self._run(monkeypatch, fake_context, data_path,
                                "/admin_gnome_name 1001 Новое", is_admin=False)
        assert "админская" in reply.await_args.args[0]
        stored = json.load(open(data_path, encoding="utf-8"))["1001"]
        assert stored["display_name"] == "Когди Изобретательный"  # не тронули

    async def test_admin_sets_name_bypassing_cas(self, tmp_path, monkeypatch, fake_context):
        data_path = self._player_file(tmp_path)
        await self._run(monkeypatch, fake_context, data_path, "/admin_gnome_name 1001 Когли Изобретательный")
        stored = json.load(open(data_path, encoding="utf-8"))["1001"]
        assert stored["display_name"] == "Когли Изобретательный"
        assert stored["display_named"] is True

    async def test_admin_resets_name(self, tmp_path, monkeypatch, fake_context):
        data_path = self._player_file(tmp_path)
        await self._run(monkeypatch, fake_context, data_path, "/admin_gnome_reset 1001")
        stored = json.load(open(data_path, encoding="utf-8"))["1001"]
        assert stored["display_name"] == ""
        assert stored["display_named"] is False

    async def test_admin_creates_defaults_and_sets(self, tmp_path, monkeypatch, fake_context):
        data_path = tmp_path / "player_data.json"
        data_path.write_text("{}", encoding="utf-8")
        await self._run(monkeypatch, fake_context, data_path, "/admin_gnome_name 555 Байт Одперчата")
        stored = json.load(open(data_path, encoding="utf-8"))["555"]
        assert stored["display_name"] == "Байт Одперчата"
        assert stored["display_named"] is True

    async def test_usage_when_no_args(self, tmp_path, monkeypatch, fake_context):
        data_path = self._player_file(tmp_path)
        reply = await self._run(monkeypatch, fake_context, data_path, "/admin_gnome_reset")
        assert "Формат" in reply.await_args.args[0]


class TestAdminExpeditionFinish:
    """/admin_gnome_finish — досрочное завершение висящей экспедиции."""

    async def _run(self, monkeypatch, fake_context, text, is_admin=True):
        monkeypatch.setattr(adv, "is_admin", lambda uid: is_admin)
        reply = AsyncMock(return_value=SimpleNamespace(chat_id=-42, message_id=950))
        await adv.admin_expedition_finish_command(
            SimpleNamespace(
                effective_user=SimpleNamespace(id=999),
                effective_message=SimpleNamespace(chat_id=-42, message_id=951, text=text, reply_text=reply),
            ),
            SimpleNamespace(
                args=text.split()[1:], user_data={},
                job_queue=None, bot=fake_context.bot,
            ),
        )
        return reply

    async def test_sends_finishing_job_for_active_expedition(self, tmp_path, monkeypatch, fake_context):
        data_path = tmp_path / "player_data.json"
        now = datetime.now(timezone.utc)
        with open(data_path, "w", encoding="utf-8") as f:
            json.dump({"555": {
                "hp": 200, "max_hp": 200, "lvl": 1,
                "active_expedition": {
                    "location_id": "dark_forest",
                    "username": "Игрок",
                    "start_time": (now - timedelta(minutes=7)).isoformat(),
                    "end_time": (now - timedelta(minutes=1)).isoformat(),
                    "chat_id": -42,
                },
            }}, f, ensure_ascii=False)
        monkeypatch.setattr(adv, "PLAYER_DATA_PATH", str(data_path))
        fake_context.bot.send_message = AsyncMock(
            return_value=SimpleNamespace(chat_id=-42, message_id=770),
        )

        reply = await self._run(monkeypatch, fake_context, "/admin_gnome_finish 555")
        # send_expedition_result отправил отчёт в чат.
        fake_context.bot.send_message.assert_awaited()
        # Экспедиция закрыта — админ получил «завершена».
        assert "завершена" in reply.await_args.args[0]
        assert reply.await_args.args[0].endswith("555") or " 555" in reply.await_args.args[0] or \
            str(555) in reply.await_args.args[0]
        # Отчёт ушёл в чат экспедиции.
        assert fake_context.bot.send_message.await_args.kwargs["chat_id"] == -42

    async def test_non_admin_denied(self, monkeypatch, fake_context):
        reply = await self._run(monkeypatch, fake_context, "/admin_gnome_finish 555", is_admin=False)
        assert "админская" in reply.await_args.args[0]
        fake_context.bot.send_message.assert_not_awaited()

    async def test_usage_without_args(self, monkeypatch, fake_context):
        reply = await self._run(monkeypatch, fake_context, "/admin_gnome_finish")
        assert "Формат" in reply.await_args.args[0]
        fake_context.bot.send_message.assert_not_awaited()


class TestForge:
    """⚒ Кузня: крафт из материалов, надевание и снятие снаряжения."""

    def _data(self, tmp_path, inventory=None, equipment=None):
        data_path = tmp_path / "player_data.json"
        with open(data_path, "w", encoding="utf-8") as f:
            json.dump({"1001": {
                "hp": 200, "max_hp": 200, "lvl": 1, "xp": 0,
                "inventory": inventory or [],
                "equipment": equipment or {},
            }}, f, ensure_ascii=False)
        return data_path

    POOL = {
        "items": {},
        "gear": {
            "wood_axe": {"name": "🪓 Топорик дровосека", "slot": "weapon", "dmg": 35},
            "resin_cap": {"name": "🧢 Смоляная шапка", "slot": "head", "zone_guard": 5},
        },
    }
    RECIPES = {"recipes": [
        {"id": "wood_axe", "result": "wood_axe", "materials": [
            {"name": "🪵 Древесина", "qty": 3},
            {"name": "👑 Смола Барона", "qty": 1},
        ]},
    ]}

    def _setup(self, tmp_path, monkeypatch, data_path):
        import importlib, tempfile, os
        pool_path = tmp_path / "global_pool.json"
        pool_path.write_text(json.dumps(self.POOL, ensure_ascii=False), encoding="utf-8")
        recipes_path = tmp_path / "recipes.json"
        recipes_path.write_text(json.dumps(self.RECIPES, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(adv, "GLOBAL_POOL_PATH", str(pool_path))
        monkeypatch.setattr(adv, "RECIPES_PATH", str(recipes_path))
        monkeypatch.setattr(adv, "PLAYER_DATA_PATH", str(data_path))

    def _query(self, data):
        return SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock())

    async def _forge_render(self, monkeypatch, fake_context, data_path, data):
        q = self._query(data)
        await adv.adventure_callback(
            SimpleNamespace(
                callback_query=q,
                effective_user=SimpleNamespace(id=1001, username="tester"),
            ),
            fake_context,
        )
        return q

    async def test_forge_screen_has_categories(self, tmp_path, monkeypatch, fake_context):
        data_path = self._data(tmp_path)
        self._setup(tmp_path, monkeypatch, data_path)

        q = await self._forge_render(monkeypatch, fake_context, data_path, "adv_forge")
        args, kwargs = q.edit_message_text.await_args
        assert "Кузня" in args[0]
        assert "штаны из берёзовой коры" in args[0]
        assert "tester" in args[0]
        # Кнопки: 5 категорий + назад; материалов нет — без пометок [N].
        labels = [b.text for row in kwargs["reply_markup"].inline_keyboard for b in row]
        assert any("РЕЦЕПТЫ ОРУЖИЯ" in t for t in labels)
        assert not any("[" in t for t in labels)
        callbacks = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
        for slot in ("weapon", "head", "body", "dick", "trinket"):
            assert f"adv_recipes_1001_{slot}" in callbacks
        assert "adv_back_o_1001" in callbacks

    async def test_foreign_back_and_to_forge_are_silent(self, tmp_path, monkeypatch, fake_context):
        """Чужой жмёт «назад»/«← кузня» на чужом экране — тихий отказ без правок."""
        data_path = self._data(tmp_path)
        self._setup(tmp_path, monkeypatch, data_path)

        for data in ("adv_back_o_1001", "adv_forgeb_1001"):
            q = self._query(data)
            await adv.adventure_callback(
                SimpleNamespace(
                    callback_query=q,
                    effective_user=SimpleNamespace(id=2222222),
                ),
                fake_context,
            )
            q.answer.assert_awaited()          # кнопку «погасили» без тоста
            q.edit_message_text.assert_not_awaited()

        # Владелец всё ещё свободно ходит: назад — в меню локаций.
        q = self._query("adv_back_o_1001")
        await adv.adventure_callback(
            SimpleNamespace(
                callback_query=q,
                effective_user=SimpleNamespace(id=1001, username="tester"),
            ),
            fake_context,
        )
        assert "Доступные локации" in q.edit_message_text.await_args.args[0]

    async def test_forge_badge_counts_craftable(self, tmp_path, monkeypatch, fake_context):
        """На кнопке категории — [N] если на N рецептов хватает материалов."""
        inventory = [
            {"name": "🪵 Древесина", "quantity": 3},
            {"name": "👑 Смола Барона", "quantity": 1},
        ]
        data_path = self._data(tmp_path, inventory=inventory)
        self._setup(tmp_path, monkeypatch, data_path)

        q = await self._forge_render(monkeypatch, fake_context, data_path, "adv_forge")
        labels = [b.text for row in q.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard for b in row]
        assert any("РЕЦЕПТЫ ОРУЖИЯ [1]" in t for t in labels)
        # С «выломать» категория даёт второй рецепт (лопата: древесина+камень — камня нет).
        assert any("ШАПОК" in t and "[1]" not in t for t in labels)

    async def test_forge_no_badge_when_nothing_craftable(self, tmp_path, monkeypatch, fake_context):
        """Ноль скова — чистая кнопка без [0]."""
        inventory = [{"name": "🪵 Древесина", "quantity": 10}]  # без смолы топор не собирается
        data_path = self._data(tmp_path, inventory=inventory)
        self._setup(tmp_path, monkeypatch, data_path)

        q = await self._forge_render(monkeypatch, fake_context, data_path, "adv_forge")
        labels = [b.text for row in q.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard for b in row]
        assert any("РЕЦЕПТЫ ОРУЖИЯ" in t and "[1]" not in t for t in labels)
        assert not any(t.endswith("[0]") or "[0]" in t for t in labels)

    async def test_forge_category_screen_recipe_counts(self, tmp_path, monkeypatch, fake_context):
        """/adv_recipes_{owner}_weapon: рецепты «нужно/есть» + запас материалов."""
        data_path = self._data(tmp_path)
        self._setup(tmp_path, monkeypatch, data_path)

        q = await self._forge_render(monkeypatch, fake_context, data_path, "adv_recipes_1001_weapon")
        args, kwargs = q.edit_message_text.await_args
        assert "Рецепты оружия" in args[0]
        assert "🪵 Древесина ×3/0" in args[0]          # пустой инвентарь
        assert "ЗАПАС МАТЕРИАЛОВ" in args[0]
        callbacks = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
        assert "adv_craftr_1001_weapon_wood_axe" in callbacks
        assert "adv_forgeb_1001" in callbacks            # ← кузня
        assert "adv_back_o_1001" in callbacks           # назад к локациям (лично)

    async def test_foreign_click_gets_cold_forge(self, tmp_path, monkeypatch, fake_context):
        """Чужой жмёт кнопку кузни → тост «не твоя кузня», инвентарь цел."""
        inventory = [{"name": "🪵 Древесина", "quantity": 3},
                     {"name": "👑 Смола Барона", "quantity": 1}]
        data_path = self._data(tmp_path, inventory=inventory)
        self._setup(tmp_path, monkeypatch, data_path)

        q = self._query("adv_craftr_1001_weapon_wood_axe")
        await adv.adventure_callback(
            SimpleNamespace(
                callback_query=q,
                effective_user=SimpleNamespace(id=2222222),  # не владелец
            ),
            fake_context,
        )
        q.edit_message_text.assert_not_awaited()
        answer_text = q.answer.await_args.args[0]
        assert "чужая кузня" in answer_text
        # Инвентарь владельца не тронут: ни предмета, ни списания.
        stored = json.load(open(data_path, encoding="utf-8"))["1001"]
        assert not any(i.get("gear_id") == "wood_axe" for i in stored["inventory"])
        assert sum(i["quantity"] for i in stored["inventory"] if i["name"] == "🪵 Древесина") == 3

    async def test_locations_list_has_forge_button(self, tmp_path, monkeypatch):
        keyboard = adv._locations_keyboard({"dark_forest": {"name": "Лес"}})
        labels = [b.text for row in keyboard.inline_keyboard for b in row]
        assert "⚒ КУЗНЯ" in labels

    async def test_craft_success_consumes_and_stores_gear(self, tmp_path, monkeypatch, fake_context):
        inventory = [{"name": "🪵 Древесина", "quantity": 3},
                     {"name": "🪵 Древесина", "quantity": 1},  # лиш detail лишняя единица не помешает
                     {"name": "👑 Смола Барона", "quantity": 1}]
        data_path = self._data(tmp_path, inventory=inventory)
        self._setup(tmp_path, monkeypatch, data_path)
        fake_context.bot.edit_message_text = AsyncMock()

        q = await self._forge_render(monkeypatch, fake_context, data_path, "adv_craftr_1001_weapon_wood_axe")

        q.answer.assert_awaited_once()
        assert "Сковано" in q.answer.await_args.args[0]
        stored = json.load(open(data_path, encoding="utf-8"))["1001"]
        wood_left = [i for i in stored["inventory"] if i["name"] == "🪵 Древесина"]
        assert wood_left == [{"name": "🪵 Древесина", "quantity": 1, "gear_id": None}] or wood_left[0]["quantity"] == 1
        gear = [i for i in stored["inventory"] if i.get("gear_id") == "wood_axe"]
        assert gear and gear[0]["name"] == "🪓 Топорик дровосека"

    async def test_craft_missing_materials_rejected(self, tmp_path, monkeypatch, fake_context):
        data_path = self._data(tmp_path, inventory=[{"name": "🪵 Древесина", "quantity": 1}])
        self._setup(tmp_path, monkeypatch, data_path)

        q = await self._forge_render(monkeypatch, fake_context, data_path, "adv_craftr_1001_weapon_wood_axe")
        q.edit_message_text.assert_not_awaited()          # экран не отрисовался
        answer_text = q.answer.await_args.args[0]
        assert "Не хватает" in answer_text
        assert "Смола" in answer_text

    async def test_equipment_command_and_roundtrip(self, tmp_path, monkeypatch, fake_context):
        """/adventure_equipment: надеть через экран команды, снять там же."""
        inventory = [{"name": "🪓 Топорик дровосека", "gear_id": "wood_axe", "quantity": 1}]
        data_path = self._data(tmp_path, inventory=inventory)
        self._setup(tmp_path, monkeypatch, data_path)

        # Команда: экран с кнопкой «НАДЕТЬ».
        reply = AsyncMock(return_value=SimpleNamespace(chat_id=-42, message_id=650))
        await adv.adventure_equipment_command(
            SimpleNamespace(
                effective_user=SimpleNamespace(id=1001, username="tester"),
                effective_message=SimpleNamespace(chat_id=-42, message_id=651, reply_text=reply),
            ),
            fake_context,
        )
        markup = reply.await_args.kwargs["reply_markup"]
        buttons = [(b.text, b.callback_data)
                   for row in markup.inline_keyboard for b in row]
        assert any("НАДЕТЬ" in t and cd == "adv_gear_on_1001_wood_axe" for t, cd in buttons)
        assert not any("СНЯТЬ" in t for t, cd in buttons)

        # Надели через колбэк — экран перерисовался с «СНЯТЬ».
        q = self._query("adv_gear_on_1001_wood_axe")
        await adv.adventure_callback(
            SimpleNamespace(callback_query=q, effective_user=SimpleNamespace(id=1001, username="tester")),
            fake_context,
        )
        stored = json.load(open(data_path, encoding="utf-8"))["1001"]
        assert stored["equipment"]["weapon"] == "wood_axe"
        redraw_args = q.edit_message_text.await_args.kwargs
        redraw_buttons = [b.callback_data
                          for row in redraw_args["reply_markup"].inline_keyboard for b in row]
        assert "adv_gear_off_1001_weapon" in redraw_buttons

        # Сняли — вернулся в инвентарь.
        q2 = self._query("adv_gear_off_1001_weapon")
        await adv.adventure_callback(
            SimpleNamespace(callback_query=q2, effective_user=SimpleNamespace(id=1001, username="tester")),
            fake_context,
        )
        stored = json.load(open(data_path, encoding="utf-8"))["1001"]
        assert stored["equipment"]["weapon"] is None
        assert any(i.get("gear_id") == "wood_axe" for i in stored["inventory"])

    async def test_equip_missing_instance_rejected(self, tmp_path, monkeypatch, fake_context):
        data_path = self._data(tmp_path)
        self._setup(tmp_path, monkeypatch, data_path)
        q = await self._forge_render(monkeypatch, fake_context, data_path, "adv_gear_on_1001_wood_axe")
        q.edit_message_text.assert_not_awaited()
        assert "нет в инвентаре" in q.answer.await_args.args[0]


class TestInventoryEquippedLine:
    """/adventure_inventory: строка «На гноме» синхронна с equipment."""

    async def _render(self, tmp_path, monkeypatch, fake_context, player):
        data_path = tmp_path / "player_data.json"
        with open(data_path, "w", encoding="utf-8") as f:
            json.dump({"1001": player}, f, ensure_ascii=False)
        monkeypatch.setattr(adv, "PLAYER_DATA_PATH", str(data_path))
        monster_pool = {
            "items": {},
            "gear": {
                "wood_axe": {"name": "🪓 Топорик дровосека", "slot": "weapon"},
                "amulet_hamster": {"name": "🧿 Оберег хомяка", "slot": "trinket"},
            },
        }
        pool_path = tmp_path / "global_pool.json"
        pool_path.write_text(json.dumps(monster_pool, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(adv, "GLOBAL_POOL_PATH", str(pool_path))

        reply = AsyncMock(return_value=SimpleNamespace(chat_id=-42, message_id=610))
        await adv.adventure_inventory_command(
            SimpleNamespace(
                effective_user=SimpleNamespace(id=1001),
                effective_message=SimpleNamespace(chat_id=-42, message_id=611, reply_text=reply),
            ),
            fake_context,
        )
        return reply.await_args.args[0]

    async def test_shows_nihuya_when_nothing_equipped(self, tmp_path, monkeypatch, fake_context):
        text = await self._render(tmp_path, monkeypatch, fake_context,
                                  {"hp": 200, "max_hp": 200, "lvl": 1})
        assert "⚙️ На гноме: нихуя" in text

    async def test_shows_equipped_names(self, tmp_path, monkeypatch, fake_context):
        text = await self._render(tmp_path, monkeypatch, fake_context, {
            "hp": 200, "max_hp": 200, "lvl": 1,
            "inventory": [{"name": "🪵 Древесина", "quantity": 2}],
            "equipment": {"weapon": "wood_axe", "trinket": "amulet_hamster"},
        })
        assert "⚙️ На гноме: 🪓 Топорик дровосека · 🎪 🧿 Оберег хомяка" in text


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
