import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import handlers.adventure as adv
import handlers.pets as pets

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
            "pet": {"id": "old_crab", "name": "🦀 Старый краб", "perks": {}},
            "inventory": [{"name": EGG, "quantity": 1}],
        })

        result = pets.hatch_pet(123)
        assert result["replaced"] == "🦀 Старый краб"
        assert _read(data_path)["pet"]["name"] == "🦀 Пёстрый хуекраб"

    def test_hatch_without_egg(self, tmp_path, monkeypatch):
        _setup(tmp_path, monkeypatch, {"hp": 200, "max_hp": 200, "inventory": []})
        assert pets.hatch_pet(123) == {"event": "empty"}


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
        assert "🐾 Питомец: 🦀 Пёстрый хуекраб" in reply.await_args.args[0]
