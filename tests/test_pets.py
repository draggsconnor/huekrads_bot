import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import handlers.adventure as adv
import handlers.pets as pets
from handlers import utils

from tests.test_battle import FakeRandom

EGG = adv.PET_EGG_NAME

PETS = {
    "crabs": {
        "piebald_crab": {"name": "🦀 Пёстрый хуекраб", "weight": 55, "perks": {"scavenger": 0.25}, "flavor": "f1"},
        "resin_crab": {"name": "🦀 Смоляной хуекраб", "weight": 25, "perks": {"shield": True}, "flavor": "f2"},
        "greedy_crab": {"name": "🦀 Хуекраб-Жадина", "weight": 20, "perks": {"greedy": 0.4}, "flavor": "f3"},
    }
}


def _setup(tmp_path, monkeypatch, player):
    data_path = tmp_path / "player_data.json"
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump({"123": player}, f, ensure_ascii=False)
    monkeypatch.setattr(adv, "PLAYER_DATA_PATH", str(data_path))

    pets_path = tmp_path / "pets.json"
    with open(pets_path, "w", encoding="utf-8") as f:
        json.dump(PETS, f, ensure_ascii=False)
    monkeypatch.setattr(pets, "PETS_PATH", str(pets_path))
    return data_path


def _read(data_path):
    with open(data_path, encoding="utf-8") as f:
        return json.load(f)["123"]


class TestNamePet:
    def _setup(self, tmp_path, monkeypatch, player):
        data_path = tmp_path / "player_data.json"
        with open(data_path, "w", encoding="utf-8") as f:
            json.dump({"123": player}, f, ensure_ascii=False)
        monkeypatch.setattr(adv, "PLAYER_DATA_PATH", str(data_path))

        pets_path = tmp_path / "pets.json"
        with open(pets_path, "w", encoding="utf-8") as f:
            json.dump(PETS, f, ensure_ascii=False)
        monkeypatch.setattr(pets, "PETS_PATH", str(pets_path))
        return data_path

    def _read(self, data_path):
        with open(data_path, encoding="utf-8") as f:
            return json.load(f)["123"]

    def test_name_once_then_locked(self, tmp_path, monkeypatch):
        data_path = self._setup(tmp_path, monkeypatch, {
            "hp": 200, "max_hp": 200,
            "pet": {"id": "piebald_crab", "name": "🦀 Пёстрый хуекраб", "perks": {}},
            "inventory": [],
        })

        result = pets.name_pet(123, "Клобук")
        assert result == {"event": "named", "name": "Клобук"}
        stored = self._read(data_path)["pet"]
        assert stored["name"] == "Клобук"
        assert stored["renamed"] is True

        again = pets.name_pet(123, "Другое Имя")
        assert again["event"] == "already_named"
        assert again["name"] == "Клобук"
        assert self._read(data_path)["pet"]["name"] == "Клобук"

    def test_name_trims_and_caps(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, {
            "hp": 200, "max_hp": 200,
            "pet": {"id": "piebald_crab", "name": "🦀 Краб", "perks": {}},
            "inventory": [],
        })
        long_name = "Й" * 30
        result = pets.name_pet(123, f"  {long_name}  ")
        assert result["event"] == "named"
        assert len(result["name"]) == 24

    def test_name_requires_pet(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, {"hp": 200, "max_hp": 200, "inventory": []})
        assert pets.name_pet(123, "Краб")["event"] == "empty"

    def test_name_rejects_blank(self, tmp_path, monkeypatch):
        data_path = self._setup(tmp_path, monkeypatch, {
            "hp": 200, "max_hp": 200,
            "pet": {"id": "piebald_crab", "name": "🦀 Краб", "perks": {}},
            "inventory": [],
        })
        assert pets.name_pet(123, "   ") == {"event": "empty_name"}
        assert not self._read(data_path)["pet"].get("renamed")


class TestHuecrabNameCommand:
    async def test_cmd_names_pet(self, tmp_path, monkeypatch, fake_context):
        data_path = tmp_path / "player_data.json"
        with open(data_path, "w", encoding="utf-8") as f:
            json.dump({"123": {
                "hp": 200, "max_hp": 200, "lvl": 1,
                "pet": {"id": "piebald_crab", "name": "🦀 Пёстрый хуекраб", "perks": {}},
            }}, f, ensure_ascii=False)
        monkeypatch.setattr(adv, "PLAYER_DATA_PATH", str(data_path))

        reply = AsyncMock(return_value=SimpleNamespace(chat_id=-42, message_id=320))
        fake_context.args = ["Ага", "Сосиск"]  # как PTB подкладывает аргументы команды
        await pets.adventure_huecrab_name_command(
            SimpleNamespace(
                effective_user=SimpleNamespace(id=123),
                effective_message=SimpleNamespace(chat_id=-42, message_id=319, reply_text=reply),
            ),
            fake_context,
        )
        assert "Ага Сосиск" in reply.await_args.args[0]
        stored = json.load(open(data_path, encoding="utf-8"))["123"]
        assert stored["pet"]["name"] == "Ага Сосиск"
        assert stored["pet"]["renamed"] is True


class TestHatchPet:
    def test_hatch_consumes_egg_and_assigns_pet(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pets, "random", FakeRandom(0.0))  # первый вид из реестра
        data_path = _setup(tmp_path, monkeypatch, {
            "hp": 200, "max_hp": 200, "lvl": 1,
            "inventory": [{"name": EGG, "quantity": 1}],
        })

        result = pets.hatch_pet(123)
        assert result["event"] == "hatched"
        assert result["pet_name"] == "🦀 Пёстрый хуекраб"
        assert result["perks"] == {"scavenger": 0.25}
        assert result["eggs_left"] == 0
        assert any("Докидка лута (25%)" in line for line in result["perk_lines"])

        stored = _read(data_path)
        assert stored["pet"]["id"] == "piebald_crab"
        assert stored["pet"]["perks"] == {"scavenger": 0.25}
        assert stored["inventory"] == []  # яйцо съедено процессом вылупления

    def test_hatch_picks_least_likely_variant(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pets, "random", FakeRandom(0.99))  # сумма 100 -> последний вид
        _setup(tmp_path, monkeypatch, {"hp": 200, "max_hp": 200, "inventory": [{"name": EGG, "quantity": 1}]})

        result = pets.hatch_pet(123)
        assert result["pet_name"] == "🦀 Хуекраб-Жадина"  # cumulative 100 == rand 99

    def test_hatch_replaces_old_pet(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pets, "random", FakeRandom(0.0))
        data_path = _setup(tmp_path, monkeypatch, {
            "hp": 200, "max_hp": 200,
            "pet": {"id": "old_crab", "name": "🦀 Старый краб", "perks": {}, "level": 2, "xp": 25},
            "inventory": [{"name": EGG, "quantity": 1}],
        })

        result = pets.hatch_pet(123)
        assert result["replaced"] == "🦀 Старый краб"
        with open(data_path, encoding="utf-8") as f:
            stored = json.load(f)["123"]
        assert stored["pet"]["name"] == "🦀 Пёстрый хуекраб"
        assert stored["pet"]["level"] == 1 and stored["pet"]["xp"] == 0
        # Старик продолжает жить в сарае со своим стажем.
        assert stored["saray"][0]["name"] == "🦀 Старый краб"
        assert stored["saray"][0]["level"] == 2
        assert stored["saray"][0]["xp"] == 25

    def test_hatch_without_egg(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, {"hp": 200, "max_hp": 200, "inventory": []})
        assert pets.hatch_pet(123) == {"event": "empty"}


class TestSaray:
    """Смена экипированного краба (вне боя)"""

    def _setup(self, tmp_path, monkeypatch, player):
        data_path = tmp_path / "player_data.json"
        with open(data_path, "w", encoding="utf-8") as f:
            json.dump({"123": player}, f, ensure_ascii=False)
        monkeypatch.setattr(adv, "PLAYER_DATA_PATH", str(data_path))

        pets_path = tmp_path / "pets.json"
        with open(pets_path, "w", encoding="utf-8") as f:
            json.dump(PETS, f, ensure_ascii=False)
        monkeypatch.setattr(pets, "PETS_PATH", str(pets_path))
        return data_path

    def _read(self, data_path):
        with open(data_path, encoding="utf-8") as f:
            return json.load(f)["123"]

    def test_swap_swaps_and_keeps_xp(self, tmp_path, monkeypatch):
        data_path = self._setup(tmp_path, monkeypatch, {
            "hp": 200, "max_hp": 200, "pet": {
                "id": "resin_crab", "name": "🦀 Смоляной хуекраб",
                "perks": {"shield": True}, "level": 4, "xp": 10, "uid": "aaa111"},
            "saray": [{"id": "piebald_crab", "name": "🦀 Пёстрый хуекраб",
                           "perks": {"scavenger": 0.25}, "level": 1, "xp": 45, "uid": "bbb222"}],
        })

        result = pets.move_to_saray(123, "bbb222")
        assert result["event"] == "swapped"
        assert result["new"] == "🦀 Пёстрый хуекраб"
        assert result["old"] == "🦀 Смоляной хуекраб"

        stored = self._read(data_path)
        assert stored["pet"]["name"] == "🦀 Пёстрый хуекраб"
        assert stored["pet"]["level"] == 1
        assert stored["saray"][0]["name"] == "🦀 Смоляной хуекраб"
        assert stored["saray"][0]["level"] == 4  # стаж сохраняется в сарае

    def test_swap_blocked_in_battle(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, {
            "hp": 200, "max_hp": 200, "active_battle": {"battle_id": "abc"},
            "pet": {"id": "resin_crab", "name": "🦀 Смоляной хуекраб", "perks": {}, "uid": "aaa111"},
            "saray": [{"id": "piebald_crab", "name": "🦀 Пёстрый хуекраб", "perks": {}, "uid": "bbb222"}],
        })
        assert pets.move_to_saray(123, "bbb222") == {"event": "battle"}

    def test_swap_unknown_uid(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, {
            "hp": 200, "max_hp": 200,
            "pet": {"id": "resin_crab", "name": "🦀 Краб", "perks": {}, "uid": "aaa111"},
            "saray": [],
        })
        assert pets.move_to_saray(123, "zzz999")["event"] == "empty"

    def test_named_flag_travels_with_crab(self, tmp_path, monkeypatch):
        """Имя — свойство конкретного краба: söldner краба снова можно назвать."""
        data_path = self._setup(tmp_path, monkeypatch, {
            "hp": 200, "max_hp": 200,
            "pet": {"id": "resin_crab", "name": "🦀 Смоляной хуекраб", "perks": {}, "uid": "aaa111"},
            "saray": [{"id": "piebald_crab", "name": "🦀 Пёстрый хуекраб", "perks": {}, "uid": "bbb222"}],
        })

        # Назвали смоляного (только сейчас экипированного).
        assert pets.name_pet(123, "Смоляк")["event"] == "named"

        # Сменили на пёстрого без имени — его можно назвать.
        pets.move_to_saray(123, "bbb222")
        assert pets.name_pet(123, "Пятнистый")["event"] == "named"

        # Вернули смоляного — имя его с ним же и ходит.
        pets.move_to_saray(123, "aaa111")
        assert pets.name_pet(123, "Ещё Раз")["event"] == "already_named"
        assert self._read(data_path)["pet"]["name"] == "Смоляк"

    async def test_command_and_pick_flow(self, tmp_path, monkeypatch, fake_context):
        monkeypatch.setattr(pets, "random", FakeRandom(0.0))
        data_path = self._setup(tmp_path, monkeypatch, {
            "hp": 200, "max_hp": 200, "lvl": 1,
            "pet": {"id": "resin_crab", "name": "🦀 Смоляной хуекраб",
                    "perks": {"shield": True}, "level": 4, "xp": 0, "uid": "aaa111"},
            "saray": [{"id": "piebald_crab", "name": "🦀 Пёстрый хуекраб",
                       "perks": {"scavenger": 0.25}, "level": 1, "xp": 0, "uid": "bbb222"}],
        })

        # 1) /adventure_saray: карточка + кнопка ВЫБРАТЬ.
        reply = AsyncMock(return_value=SimpleNamespace(chat_id=-42, message_id=400))
        await pets.adventure_saray_command(
            SimpleNamespace(
                effective_user=SimpleNamespace(id=123),
                effective_message=SimpleNamespace(chat_id=-42, message_id=399, reply_text=reply),
            ),
            fake_context,
        )
        commands_text = reply.await_args.args[0]
        assert "САРАЙ ХУЕКРАБОВ" in commands_text
        texts = [b.text for row in reply.await_args.kwargs["reply_markup"].inline_keyboard for b in row]
        assert any("ВЫБРАТЬ" in t for t in texts)

        # 2) ВЫБРАТЬ: пикер с сарасыми крабами.
        query = SimpleNamespace(
            data="saray_123_open", answer=AsyncMock(), edit_message_text=AsyncMock(),
            message=SimpleNamespace(chat_id=-42, message_id=400),
        )
        await pets.saray_callback(
            SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=123)),
            fake_context,
        )
        pick = query.edit_message_text.await_args.kwargs
        assert "Кого берём" in pick["text"]
        pick_texts = [b.text for row in pick["reply_markup"].inline_keyboard for b in row]
        assert any("Пёстрый" in t and "ур. 1" in t for t in pick_texts)

        # 3) Выбор краба: смена + обновлённый список.
        query2 = SimpleNamespace(
            data="saray_123_swap_bbb222", answer=AsyncMock(), edit_message_text=AsyncMock(),
            message=SimpleNamespace(chat_id=-42, message_id=400),
        )
        await pets.saray_callback(
            SimpleNamespace(callback_query=query2, effective_user=SimpleNamespace(id=123)),
            fake_context,
        )
        swapped = query2.edit_message_text.await_args.kwargs["text"]
        assert "На смену выходит" in swapped
        stored = json.load(open(data_path, encoding="utf-8"))["123"]
        assert stored["pet"]["uid"] == "bbb222"
        assert stored["saray"][0]["uid"] == "aaa111"


class TestHuecrabCard:
    """/adventure_huecrab: карточка питомца"""

    def test_card_full(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, {
            "hp": 200, "max_hp": 200,
            "pet": {"id": "piebald_crab", "name": "🦀 Пёстрый хуекраб",
                    "perks": {"scavenger": 0.25}, "level": 2, "xp": 30},
            "inventory": [],
        })

        text = pets.render_huecrab_card({"pet": {
            "id": "piebald_crab", "name": "🦀 Пёстрый хуекраб",
            "perks": {"scavenger": 0.25}, "level": 2, "xp": 30,
        }})
        assert "🦀 Пёстрый хуекраб" in text
        assert "<i>f1</i>" in text                     # полный флавор из реестра
        assert "Уровень 2 (30/100 XP)" in text
        assert "Докидка лута (25%)" in text

    def test_card_without_pet(self):
        text = pets.render_huecrab_card({"pet": None})
        assert "Питомца пока нет" in text
        assert "ВЫЛУПИТЬ" in text

    def test_card_rename_button_for_unnamed_crab(self):
        # Без имени → кнопка naming с адресом владельца.
        player = {"pet": {"id": "piebald_crab", "name": "🦀 Пёстрый хуекраб",
                          "perks": {}, "level": 1, "xp": 0}}
        markup = pets._huecrab_card_keyboard(7, player)
        button = markup.inline_keyboard[0][0]
        assert "ДАТЬ ИМЯ" in button.text
        assert button.callback_data == "hcrab_7_rename"

    def test_no_rename_button_for_named_crab(self):
        player = {"pet": {"id": "piebald_crab", "name": "🦀 Байт",
                          "perks": {}, "renamed": True, "level": 1, "xp": 0}}
        assert pets._huecrab_card_keyboard(7, player) is None

    async def test_rename_toggle_pends_and_cancels(self, tmp_path, monkeypatch, fake_context):
        data_path = tmp_path / "player_data.json"
        with open(data_path, "w", encoding="utf-8") as f:
            json.dump({"123": {
                "hp": 200, "max_hp": 200, "lvl": 1,
                "pet": {"id": "piebald_crab", "name": "🦀 Пёстрый хуекраб",
                        "perks": {}, "level": 1, "xp": 0},
            }}, f, ensure_ascii=False)
        monkeypatch.setattr(adv, "PLAYER_DATA_PATH", str(data_path))

        query = SimpleNamespace(
            data="hcrab_123_rename", answer=AsyncMock(),
            message=SimpleNamespace(chat_id=-42, message_id=400),
        )
        update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=123))

        await pets.huecrab_card_callback(update, fake_context)
        assert fake_context.user_data[pets.PENDING_RENAME_KEY] == -42
        query.answer.assert_awaited()  # тост с инструкцией

        # Повторное нажатие — отмена.
        await pets.huecrab_card_callback(update, fake_context)
        assert not fake_context.user_data.get(pets.PENDING_RENAME_KEY)
        query.answer.assert_awaited_with("⌨️ Ожидание имени отменено.")

    async def test_text_names_pet_and_cleans_up(self, tmp_path, monkeypatch, fake_context):
        data_path = tmp_path / "player_data.json"
        with open(data_path, "w", encoding="utf-8") as f:
            json.dump({"123": {
                "hp": 200, "max_hp": 200, "lvl": 1,
                "pet": {"id": "piebald_crab", "name": "🦀 Пёстрый хуекраб",
                        "perks": {}, "level": 1, "xp": 0},
            }}, f, ensure_ascii=False)
        monkeypatch.setattr(adv, "PLAYER_DATA_PATH", str(data_path))

        fake_context.user_data[pets.PENDING_RENAME_KEY] = -42
        reply = AsyncMock(return_value=SimpleNamespace(chat_id=-42, message_id=401))
        message = SimpleNamespace(
            text="Байт", chat_id=-42, message_id=402, reply_text=reply,
        )
        await pets.pet_name_text_handler(
            SimpleNamespace(
                effective_user=SimpleNamespace(id=123),
                effective_message=message,
            ),
            fake_context,
        )

        assert "Байт" in reply.await_args.args[0]
        fake_context.bot.delete_message.assert_awaited_once_with(chat_id=-42, message_id=402)
        stored = json.load(open(data_path, encoding="utf-8"))["123"]
        assert stored["pet"]["name"] == "Байт"
        assert stored["pet"]["renamed"] is True

    async def test_text_passes_through_without_pending(self, fake_context):
        reply = AsyncMock()
        message = SimpleNamespace(text="обычный текст", chat_id=-42, message_id=5, reply_text=reply)
        await pets.pet_name_text_handler(
            SimpleNamespace(
                effective_user=SimpleNamespace(id=123),
                effective_message=message,
            ),
            fake_context,
        )
        reply.assert_not_awaited()
        fake_context.bot.delete_message.assert_not_awaited()

    async def test_text_ignored_in_other_chat(self, tmp_path, monkeypatch, fake_context):
        # Флаг ожидания установлен для -42, текст пришёл в другой чат — мимо.
        data_path = tmp_path / "player_data.json"
        with open(data_path, "w", encoding="utf-8") as f:
            json.dump({"123": {
                "hp": 200, "max_hp": 200, "lvl": 1,
                "pet": {"id": "piebald_crab", "name": "🦀 Пёстрый хуекраб",
                        "perks": {}, "level": 1, "xp": 0},
            }}, f, ensure_ascii=False)
        monkeypatch.setattr(adv, "PLAYER_DATA_PATH", str(data_path))

        fake_context.user_data[pets.PENDING_RENAME_KEY] = -42
        reply = AsyncMock()
        message = SimpleNamespace(text="Байт", chat_id=777, message_id=6, reply_text=reply)
        await pets.pet_name_text_handler(
            SimpleNamespace(
                effective_user=SimpleNamespace(id=123),
                effective_message=message,
            ),
            fake_context,
        )
        reply.assert_not_awaited()
        assert fake_context.user_data[pets.PENDING_RENAME_KEY] == -42  # флаг жив

    async def test_command_renders_card(self, tmp_path, monkeypatch, fake_context):
        _setup(tmp_path, monkeypatch, {
            "hp": 200, "max_hp": 200, "lvl": 1,
            "pet": {"id": "piebald_crab", "name": "🦀 Пёстрый хуекраб",
                    "perks": {"scavenger": 0.25}, "level": 1, "xp": 0},
            "inventory": [],
        })

        reply = AsyncMock(return_value=SimpleNamespace(chat_id=-42, message_id=310))
        await pets.adventure_huecrab_command(
            SimpleNamespace(
                effective_user=SimpleNamespace(id=123),
                effective_message=SimpleNamespace(chat_id=-42, message_id=309, reply_text=reply),
            ),
            fake_context,
        )
        text = reply.await_args.args[0]
        assert "🦀 Пёстрый хуекраб" in text
        # Самоочистка подключена (команда + ответ).
        scheduled = [
            call for call in fake_context.job_queue.calls
            if call[0] is utils.self_delete_job
        ]
        assert len(scheduled) == 2


class TestPetFlowUI:
    async def test_hatch_button_and_callback(self, tmp_path, monkeypatch, fake_context):
        monkeypatch.setattr(pets, "random", FakeRandom(0.0))  # первый вид: Пёстрый
        data_path = _setup(tmp_path, monkeypatch, {
            "hp": 200, "max_hp": 200, "lvl": 1, "inventory": [{"name": EGG, "quantity": 1}],
        })

        # 1) Инвентарь с яйцом показывает кнопку ВЫЛУПИТЬ.
        reply = AsyncMock(return_value=SimpleNamespace(chat_id=-42, message_id=200))
        await adv.adventure_inventory_command(
            SimpleNamespace(
                effective_user=SimpleNamespace(id=123),
                effective_message=SimpleNamespace(chat_id=-42, message_id=199, reply_text=reply),
            ),
            fake_context,
        )
        texts = [b.text for row in reply.await_args.kwargs["reply_markup"].inline_keyboard for b in row]
        assert any("ВЫЛУПИТЬ" in t for t in texts)

        # 2) Кнопка вылупления рождает краба и чистит яйцо из инвентаря.
        query = SimpleNamespace(
            data="invt_123_hatch", answer=AsyncMock(), edit_message_text=AsyncMock(),
            message=SimpleNamespace(chat_id=-42, message_id=200),
        )
        await adv.inventory_callback(
            SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=123)),
            fake_context,
        )
        shown = query.edit_message_text.await_args.kwargs["text"]
        assert "🥚 Из яйца вылупляется" in shown
        assert f"🐾" in shown or "Пёстрый" in shown

        stored = _read(data_path)
        assert stored["pet"]["id"] == "piebald_crab"
        assert stored["inventory"] == []

    async def test_stats_show_pet(self, tmp_path, monkeypatch, fake_context):
        _setup(tmp_path, monkeypatch, {
            "hp": 200, "max_hp": 200, "lvl": 1,
            "pet": {"id": "piebald_crab", "name": "🦀 Пёстрый хуекраб", "perks": {}},
            "inventory": [],
        })

        reply = AsyncMock(return_value=SimpleNamespace(chat_id=-42, message_id=300))
        await adv.adventure_stats_command(
            SimpleNamespace(
                effective_user=SimpleNamespace(id=123, username="tester", first_name="Tester"),
                effective_message=SimpleNamespace(chat_id=-42, message_id=305, reply_text=reply),
            ),
            fake_context,
        )
        assert "🐾 Питомец: 🦀 Пёстрый хуекраб (ур. 1)" in reply.await_args.args[0]
