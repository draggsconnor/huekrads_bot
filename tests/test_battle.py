import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import handlers.adventure as adv
import handlers.battle as battle


class FakeRandom:
    """Детерминированная замена random: 0.0 / первый элемент / минимум."""

    def __init__(self, random_value=0.0):
        self.random_value = random_value

    def random(self):
        return self.random_value

    def choice(self, seq):
        return seq[0]

    def randint(self, a, b):
        return a

    def uniform(self, a, b):
        return a


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


LOCS = {
    "test_loc": {
        "id": "test_loc",
        "name": "Тестовая локация",
        "encounter_chance": 1.0,
        "requirements": {"min_level": 1},
        "resources": [{"id": "res", "name": "🌿 Хвоя", "chance": 1.0}],
        "bosses": [
            {
                "id": "test_boss",
                "name": "Тестовый Босс",
                "display_name": "👑 Тестовый Босс",
                "encounter_chance": 0.0,
                "base_reward": 25,
                "loot": [
                    {
                        "item_id": "boss_trophy",
                        "name": "👑 Трофей босса",
                        "quantity_min": 1,
                        "quantity_max": 1,
                        "chance": 1.0,
                    }
                ],
            }
        ],
    }
}

MOBS = {
    "battle_defaults": {
        "common": {"win_hits": 1, "dmg_per_hit": 25},
        "uncommon": {"win_hits": 2, "dmg_per_hit": 40},
        "rare": {"win_hits": 2, "dmg_per_hit": 55},
        "boss": {"win_hits": 3, "dmg_per_hit": 60},
    },
    "specific_mobs": {
        "test_loc": [
            {
                # Реальный id: имя/описание резолвятся из adventure.yaml
                "id": "bush_with_requirements",
                "name": "Inline Куст",
                "encounter_chance": 1.0,
                "tier": "common",
                "battle": {"win_hits": 1, "dmg_per_hit": 25},
                "description": "inline desc",
            }
        ]
    },
    "encounter_weights": {"common": 0.5, "uncommon": 0.3, "rare": 0.15, "boss": 0.05},
    "mob_types": {
        "common": {"names": {"test_loc": ["Слепой крыс"]}, "stats": {}},
        "uncommon": {"names": {"test_loc": ["Тёмный эльф"]}, "stats": {}},
        "rare": {"names": {"test_loc": ["Лесной король"]}, "stats": {}},
    },
    "loot_table": {
        "common": {"items": ["herb", "stone"]},
        "uncommon": {"items": ["magic_crystal"]},
        "rare": {"items": ["ancient_relic"]},
        "boss": {"items": ["boss_token"]},
    },
}

POOL = {"items": {"herb": {"name": "🌿 Трава"}, "stone": {"name": "🪨 Камень"}}, "bosses": []}


def _setup_paths(tmp_path, monkeypatch, *, locations=LOCS, mobs=MOBS, pool=POOL):
    data_path = tmp_path / "player_data.json"
    loc_path = tmp_path / "locations.json"
    mobs_path = tmp_path / "mobs.json"
    pool_path = tmp_path / "global_pool.json"
    for path, data in ((loc_path, locations), (mobs_path, mobs), (pool_path, pool)):
        _write_json(path, data)
    monkeypatch.setattr(adv, "PLAYER_DATA_PATH", str(data_path))
    monkeypatch.setattr(adv, "LOCATIONS_PATH", str(loc_path))
    monkeypatch.setattr(adv, "MOBS_PATH", str(mobs_path))
    monkeypatch.setattr(adv, "GLOBAL_POOL_PATH", str(pool_path))
    return data_path


def _expedition(*, encounter=True, minutes_end=5):
    start = datetime.now(timezone.utc) - timedelta(minutes=10)
    end = datetime.now(timezone.utc) + timedelta(minutes=minutes_end)
    expedition = {
        "location_id": "test_loc",
        "location_name": "Тестовая локация",
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "duration_seconds": 900,
        "chat_id": 42,
        "username": "tester",
        "encounter_scheduled": encounter,
    }
    if encounter:
        expedition["encounter_at"] = (start + timedelta(seconds=60)).isoformat()
    return expedition


def _write_player(data_path, player):
    _write_json(data_path, {"123": player})


def _read_player(data_path):
    return _read_json(data_path)["123"]


def _battle(**overrides):
    state = {
        "battle_id": "abc123",
        "location_id": "test_loc",
        "chat_id": 42,
        "username": "tester",
        "enemy": {
            "name": "Куст",
            "tier": "common",
            "win_hits": 1,
            "dmg_per_hit": 25,
            "base_reward": 0,
            "loot": [],
            "pool_items": [],
        },
        "round": 1,
        "phase": "defense",
        "enemy_attack": "head",
        "enemy_block": "body",
        "player_attack": None,
        "player_block": None,
        "auto_attack": False,
        "auto_block": False,
        "phase_deadline": None,
        "hits": 0,
        "dmg_taken": 0,
        "journal": [],
        "message_id": None,
    }
    state.update(overrides)
    return state


class TestPlanEncounter:
    def test_no_encounter_when_chance_fails(self, monkeypatch):
        monkeypatch.setattr(battle, "random", FakeRandom(0.99))
        assert battle.plan_encounter({"encounter_chance": 0.5}, 300) is None

    def test_encounter_delay_inside_window(self, monkeypatch):
        monkeypatch.setattr(battle, "random", FakeRandom(0.0))
        # random()=0.0 < 0.5; uniform(30, 240) -> 30
        assert battle.plan_encounter({"encounter_chance": 0.5}, 300) == 30.0

    def test_short_expedition_window(self, monkeypatch):
        monkeypatch.setattr(battle, "random", FakeRandom(0.0))
        # latest = max(31.0, 0) -> uniform(30, 31)
        assert battle.plan_encounter({"encounter_chance": 1.0}, 60) == 30.0


class TestBuildBattleEnemy:
    def test_boss_selected_with_battle_params(self, tmp_path, monkeypatch):
        locations = json.loads(json.dumps(LOCS))
        locations["test_loc"]["bosses"][0]["encounter_chance"] = 1.0
        locations["test_loc"]["bosses"][0]["battle"] = {"win_hits": 2, "dmg_per_hit": 50}
        _setup_paths(tmp_path, monkeypatch, locations=locations)
        monkeypatch.setattr(battle, "random", FakeRandom(0.0))

        enemy = battle.build_battle_enemy("test_loc")
        assert enemy["tier"] == "boss"
        assert enemy["name"] == "👑 Тестовый Босс"
        assert enemy["win_hits"] == 2
        assert enemy["dmg_per_hit"] == 50
        assert enemy["loot"][0]["item_id"] == "boss_trophy"

    def test_specific_mob_from_yaml_name(self, tmp_path, monkeypatch):
        _setup_paths(tmp_path, monkeypatch)
        monkeypatch.setattr(battle, "random", FakeRandom(0.0))

        enemy = battle.build_battle_enemy("test_loc")
        # Имя берётся из adventure.yaml, а не из инлайн-поля моба
        assert enemy["name"] == "🌳Куст с предъявами"
        assert enemy["tier"] == "common"
        assert enemy["win_hits"] == 1
        assert enemy["dmg_per_hit"] == 25
        assert enemy["pool_items"] == ["herb", "stone"]

    def test_pine_barren_minion_resolves_yaml_text(self, tmp_path, monkeypatch):
        mobs = json.loads(json.dumps(MOBS))
        mobs["specific_mobs"]["test_loc"] = [
            *mobs["specific_mobs"]["test_loc"],
            {
                "id": "pine_barren_minion",
                "name": "Inline Слуга",
                "encounter_chance": 1.0,
                "tier": "uncommon",
                "battle": {"win_hits": 2, "dmg_per_hit": 35},
                "description": "inline desc",
            },
        ]
        _setup_paths(tmp_path, monkeypatch, mobs=mobs)
        monkeypatch.setattr(battle, "random", FakeRandom(0.85))
        # Веса: куст 1.0 + слуга 1.0 -> 0.85 * 2 = 1.7 > 1.0, попадаем в слугу

        enemy = battle.build_battle_enemy("test_loc")
        assert enemy["id"] == "pine_barren_minion"
        # Имя и описание берутся из adventure.yaml, а не из инлайн-фолбэка
        assert enemy["name"] == "🌲Смоляной слуга"
        assert "Барона" in enemy["desc"]
        assert "<b>Тактика:</b>" in enemy["desc"]
        assert enemy["tier"] == "uncommon"
        assert enemy["win_hits"] == 2
        assert enemy["dmg_per_hit"] == 35

    def test_generated_mob_by_rarity(self, tmp_path, monkeypatch):
        mobs = json.loads(json.dumps(MOBS))
        mobs["specific_mobs"]["test_loc"] = []
        _setup_paths(tmp_path, monkeypatch, mobs=mobs)
        monkeypatch.setattr(battle, "random", FakeRandom(0.6))
        # 0.6: common (0.5) не хватает; uncommon (0.5+0.3=0.8) покрывает
        enemy = battle.build_battle_enemy("test_loc")
        assert enemy["tier"] == "uncommon"
        assert enemy["name"] == "Тёмный эльф"
        assert enemy["win_hits"] == 2
        assert enemy["dmg_per_hit"] == 40

    def test_abandoned_mine_roster(self, tmp_path, monkeypatch):
        """Шахта: специфичные мобы из реальных данных, редкости и лор."""
        import os

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        data_dir = os.path.join(root, "data", "adventure")
        monkeypatch.setattr(adv, "PLAYER_DATA_PATH", os.path.join(tmp_path, "players.json"))
        monkeypatch.setattr(adv, "LOCATIONS_PATH", os.path.join(data_dir, "locations.json"))
        monkeypatch.setattr(adv, "MOBS_PATH", os.path.join(data_dir, "mobs.json"))
        monkeypatch.setattr(adv, "GLOBAL_POOL_PATH", os.path.join(data_dir, "global_pool.json"))
        monkeypatch.setattr(battle, "random", FakeRandom(seq=[0.9, 0.9, 0.0]))
        # 0.9 ×2: мимо тролля (35%) и шамана (25%); 0.0: первый из весового пула мобов

        enemy = battle.build_battle_enemy("abandoned_mine")
        assert enemy["id"] == "huekrad_ghost"
        assert enemy["name"] == "👻 Призрак хуекрада"
        assert enemy["tier"] == "common"          # 1 попадание / 25 урона
        assert enemy["win_hits"] == 1
        assert enemy["dmg_per_hit"] == 25
        assert enemy["desc"]                      # лорное описание подтянулось

        # Ростер целиком и разбиение по редкостям проверяет test_mine_mobs_described_in_data.

    def test_mine_mobs_described_in_data(self):
        """Ростер шахты: 12 мобов, по 4 на редкость, у всех лорные описания."""
        with open("data/adventure/mobs.json", encoding="utf-8") as f:
            mobs = json.load(f)
        roster = mobs["specific_mobs"]["abandoned_mine"]
        assert len(roster) == 12
        from collections import Counter
        counts = Counter(m["tier"] for m in roster)
        assert counts == {"common": 4, "uncommon": 4, "rare": 4}
        assert all(m.get("description") for m in roster)
        # Имена из mob_types больше не дублируют шахту — единственный источник.
        for tier in ("common", "uncommon", "rare"):
            assert "abandoned_mine" not in mobs["mob_types"][tier]["names"]


class TestRollLoot:
    def test_roll_loot_respects_chances(self):
        loot_table = [
            {"item_id": "common_item", "name": "Common", "chance": 1.0, "quantity_min": 1, "quantity_max": 1},
            {"item_id": "rare_item", "name": "Rare", "chance": 0.0, "quantity_min": 1, "quantity_max": 1},
        ]
        result = battle._roll_loot(loot_table)
        item_ids = [r["item_id"] for r in result]
        assert "common_item" in item_ids
        assert "rare_item" not in item_ids

    def test_roll_loot_empty_table(self):
        assert battle._roll_loot([]) == []

    def test_roll_loot_variable_quantity(self):
        loot_table = [
            {"item_id": "item", "name": "Item", "chance": 1.0, "quantity_min": 2, "quantity_max": 5},
        ]
        result = battle._roll_loot(loot_table)
        assert len(result) == 1
        assert 2 <= result[0]["quantity"] <= 5


class TestResolveRound:
    """Чистая логика раунда (без Telegram)"""

    def test_hit_and_block(self):
        b = _battle()
        b["player_attack"] = "head"   # != enemy_block "body" -> попадание
        b["player_block"] = "head"    # == enemy_attack "head" -> блок
        with_block = b["player_block"]
        outcome = battle._resolve_round(b, {"hp": 200})
        assert outcome == "victory"   # win_hits=1
        assert b["hits"] == 1
        assert b["dmg_taken"] == 0
        # Победный хит гасит ответный удар
        assert b["journal"][-1]["blocked"] is None

    def test_win_hit_cancels_counter(self):
        enemy = {"name": "Босс", "tier": "boss", "win_hits": 2, "dmg_per_hit": 60,
                 "base_reward": 25, "loot": [], "pool_items": []}
        b = _battle(enemy=enemy, hits=1)
        b["player_attack"] = "head"   # != enemy_block -> победное попадание
        b["player_block"] = "body"    # неверный блок, но ответки не будет
        assert battle._resolve_round(b, {"hp": 10}) == "victory"
        assert b["dmg_taken"] == 0

    def test_wrong_block_deals_damage(self):
        enemy = {"name": "Эльф", "tier": "uncommon", "win_hits": 2, "dmg_per_hit": 40,
                 "base_reward": 0, "loot": [], "pool_items": []}
        b = _battle(enemy=enemy)
        b["player_attack"] = "body"   # == enemy_block -> промах
        b["player_block"] = "body"    # != enemy_attack "head" -> урон
        outcome = battle._resolve_round(b, {"hp": 200})
        assert outcome == "continue"
        assert b["dmg_taken"] == 40
        assert b["hits"] == 0

    def test_defeat_when_damage_reaches_hp(self):
        enemy = {"name": "Босс", "tier": "boss", "win_hits": 5, "dmg_per_hit": 60,
                 "base_reward": 0, "loot": [], "pool_items": []}
        b = _battle(enemy=enemy, dmg_taken=150)
        b["player_attack"] = "body"   # промах
        b["player_block"] = "body"    # неверный блок
        assert battle._resolve_round(b, {"hp": 200}) == "defeat"
        assert b["dmg_taken"] == 210


class TestRecordBattleChoice:
    def _prepare(self, tmp_path, monkeypatch, player):
        data_path = _setup_paths(tmp_path, monkeypatch)
        _write_player(data_path, player)
        return data_path

    def test_full_flow_victory(self, tmp_path, monkeypatch):
        # FakeRandom: зоны врага = seq[0] = "head" (атака и блок)
        monkeypatch.setattr(battle, "random", FakeRandom(0.0))
        data_path = self._prepare(tmp_path, monkeypatch, {
            "hp": 200, "max_hp": 200, "lvl": 1, "xp": 0,
            "inventory": [], "active_expedition": _expedition(),
        })

        state = battle.try_start_battle(123)
        assert state is not None
        bid = state["battle_id"]
        assert state["enemy"]["name"] == "🌳Куст с предъявами"
        assert state["phase"] == "attack"

        # Атака: "body" != enemy_block "head" -> попадание (win_hits=1)
        action = battle.record_battle_choice(123, bid, "attack", "body")
        assert action["event"] == "phase"
        assert action["battle"]["phase"] == "defense"

        # Блок: победное попадание уже в кармане — раунд решается сразу
        action = battle.record_battle_choice(123, bid, "block", "head")
        assert action["event"] == "victory"

        stored = _read_player(data_path)
        assert stored["active_battle"] is None
        assert stored["active_expedition"] is not None  # экспедиция продолжается
        assert stored["xp"] == 10  # common: +10 XP
        assert stored["wins"] == 1
        assert stored["total_fights"] == 1
        assert stored["active_expedition"]["battle_log"][0]["enemy"] == "🌳Куст с предъявами"
        # дроп с шансом 50%: random()=0.0 -> предмет из общего каталога
        assert any(i["name"] == "🌿 Трава" for i in stored["inventory"])

    def test_defeat_claims_expedition(self, tmp_path, monkeypatch):
        monkeypatch.setattr(battle, "random", FakeRandom(0.0))
        data_path = self._prepare(tmp_path, monkeypatch, {
            "hp": 30, "max_hp": 200, "lvl": 1, "xp": 0,
            "inventory": [], "active_expedition": _expedition(),
        })

        state = battle.try_start_battle(123)
        bid = state["battle_id"]
        # Враг всегда атакует/блокирует "head": атака "head" = промах,
        # блок "body" = урон 25. HP 30 -> смерть на втором раунде.
        action = {"event": "phase"}
        for _ in range(2):
            action = battle.record_battle_choice(123, bid, "attack", "head")
            assert action["event"] == "phase"
            action = battle.record_battle_choice(123, bid, "block", "body")
            if action["event"] != "phase":
                break

        assert action["event"] == "defeat"

        stored = _read_player(data_path)
        assert stored["active_battle"] is None
        assert stored["active_expedition"] is None  # досрочное возвращение
        assert stored["hp"] == 1
        assert stored["xp"] == 0
        assert stored["total_fights"] == 1
        assert stored["wins"] == 0
        # Минимальный лут: один ресурс с шансом 25% (random()=0.0 -> выдан)
        assert [i["name"] for i in stored["inventory"]] == ["🌿 Хвоя"]

    def test_stale_battle_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr(battle, "random", FakeRandom(0.0))
        data_path = self._prepare(tmp_path, monkeypatch, {
            "hp": 200, "max_hp": 200, "lvl": 1,
            "active_expedition": _expedition(),
        })
        state = battle.try_start_battle(123)
        assert state is not None

        action = battle.record_battle_choice(123, "wrong0", "attack", "head")
        assert action["event"] == "stale"

    def test_late_choice_in_wrong_phase(self, tmp_path, monkeypatch):
        monkeypatch.setattr(battle, "random", FakeRandom(0.0))
        data_path = self._prepare(tmp_path, monkeypatch, {
            "hp": 200, "max_hp": 200, "lvl": 1,
            "active_expedition": _expedition(),
        })
        state = battle.try_start_battle(123)
        bid = state["battle_id"]

        battle.record_battle_choice(123, bid, "attack", "body")
        # Повторный выбор атаки, когда уже идёт фаза блока
        action = battle.record_battle_choice(123, bid, "attack", "head")
        assert action["event"] == "late"


class TestTryStartBattle:
    def _prepare(self, tmp_path, monkeypatch, player):
        data_path = _setup_paths(tmp_path, monkeypatch)
        _write_player(data_path, player)
        return data_path

    def test_requires_scheduled_encounter(self, tmp_path, monkeypatch):
        data_path = self._prepare(tmp_path, monkeypatch, {
            "hp": 200, "max_hp": 200, "active_expedition": _expedition(encounter=False),
        })
        assert battle.try_start_battle(123) is None

    def test_skips_after_first_battle(self, tmp_path, monkeypatch):
        expedition = _expedition()
        expedition["battle_log"] = [
            {"enemy": "Куст", "result": "victory", "rounds": 2, "xp": 10, "item": None}
        ]
        self._prepare(tmp_path, monkeypatch, {
            "hp": 200, "max_hp": 200, "active_expedition": expedition,
        })
        assert battle.try_start_battle(123) is None

    def test_waits_for_encounter_time(self, tmp_path, monkeypatch):
        expedition = _expedition()
        expedition["encounter_at"] = (
            datetime.now(timezone.utc) + timedelta(minutes=5)
        ).isoformat()
        self._prepare(tmp_path, monkeypatch, {
            "hp": 200, "max_hp": 200, "active_expedition": expedition,
        })
        assert battle.try_start_battle(123) is None

    def test_no_battle_for_expired_expedition(self, tmp_path, monkeypatch):
        data_path = self._prepare(tmp_path, monkeypatch, {
            "hp": 200, "max_hp": 200, "active_expedition": _expedition(minutes_end=-1),
        })
        assert battle.try_start_battle(123) is None
        assert _read_player(data_path)["active_expedition"]["encounter_scheduled"] is False

    def test_starts_battle_when_due(self, tmp_path, monkeypatch):
        monkeypatch.setattr(battle, "random", FakeRandom(0.0))
        data_path = self._prepare(tmp_path, monkeypatch, {
            "hp": 200, "max_hp": 200, "active_expedition": _expedition(),
        })
        state = battle.try_start_battle(123)
        assert state is not None
        assert state["phase"] == "attack"
        assert state["round"] == 1
        assert _read_player(data_path)["active_battle"]["battle_id"] == state["battle_id"]


class TestAdvanceBattle:
    def _prepare(self, tmp_path, monkeypatch, battle_state):
        data_path = _setup_paths(tmp_path, monkeypatch)
        _write_player(data_path, {
            "hp": 200, "max_hp": 200, "xp": 0, "inventory": [],
            "active_expedition": _expedition(),
            "active_battle": battle_state,
        })
        return data_path

    def test_noop_before_deadline(self, tmp_path, monkeypatch):
        state = _battle()
        state["phase_deadline"] = (
            datetime.now(timezone.utc) + timedelta(seconds=30)
        ).isoformat()
        self._prepare(tmp_path, monkeypatch, state)
        assert battle.advance_battle(123) is None

    def test_auto_fills_attack_and_moves_to_defense(self, tmp_path, monkeypatch):
        monkeypatch.setattr(battle, "random", FakeRandom(0.0))
        state = _battle(phase="attack")
        state["phase_deadline"] = (
            datetime.now(timezone.utc) - timedelta(seconds=5)
        ).isoformat()
        data_path = self._prepare(tmp_path, monkeypatch, state)

        action = battle.advance_battle(123)
        assert action["event"] == "phase"
        advanced = action["battle"]
        assert advanced["phase"] == "defense"
        assert advanced["player_attack"] == "head"  # случайная авто-зона
        assert advanced["auto_attack"] is True

    def test_auto_resolves_round_on_defense_deadline(self, tmp_path, monkeypatch):
        monkeypatch.setattr(battle, "random", FakeRandom(0.0))
        enemy = {"name": "Эльф", "tier": "uncommon", "win_hits": 2, "dmg_per_hit": 40,
                 "base_reward": 0, "loot": [], "pool_items": []}
        state = _battle(enemy=enemy)
        state["player_attack"] = "head"  # != enemy_block "body" -> попадание 1/2
        state["phase_deadline"] = (
            datetime.now(timezone.utc) - timedelta(seconds=5)
        ).isoformat()
        data_path = self._prepare(tmp_path, monkeypatch, state)

        action = battle.advance_battle(123)
        assert action["event"] == "phase"  # продолжается: 1/2 попаданий
        stored = _read_player(data_path)
        nb = stored["active_battle"]
        assert nb["hits"] == 1
        assert nb["dmg_taken"] == 0
        # Авторазметка зафиксирована в журнале раунда 1 (в новом раунде флаги сброшены)
        assert nb["journal"][0]["auto"] is True
        assert nb["journal"][0]["hit"] is True
        assert nb["journal"][0]["blocked"] is True
        assert nb["round"] == 2
        assert nb["phase"] == "attack"


class TestAbortBattle:
    def test_abort_clears_battle(self, tmp_path, monkeypatch):
        data_path = _setup_paths(tmp_path, monkeypatch)
        _write_player(data_path, {
            "hp": 200, "max_hp": 200, "inventory": [],
            "active_expedition": _expedition(),
            "active_battle": _battle(),
        })
        assert battle.abort_battle(123) is True
        assert _read_player(data_path)["active_battle"] is None
        assert battle.abort_battle(123) is False


class TestApplyBattleVictoryLoot:
    def _prepare(self, tmp_path, monkeypatch):
        data_path = tmp_path / "player_data.json"
        _setup_paths(tmp_path, monkeypatch)
        _write_player(data_path, {"hp": 200, "max_hp": 200, "xp": 0, "inventory": []})
        return data_path

    def _player(self, data_path):
        return _read_player(data_path)

    def test_special_loot_and_pool_roll_both_apply(self, tmp_path, monkeypatch):
        # Слуга: свой лут (яблоко) дропается И пул тира — независимо.
        monkeypatch.setattr(battle, "random", FakeRandom(0.0))
        data_path = self._prepare(tmp_path, monkeypatch)
        player = self._player(data_path)

        state = _battle(enemy={
            "name": "Слуга", "tier": "uncommon", "win_hits": 2, "dmg_per_hit": 35,
            "base_reward": 0,
            "loot": [{"item_id": "resin_apple", "name": "🍎 Смоляное яблоко",
                      "quantity_min": 1, "quantity_max": 1, "chance": 1.0}],
            "pool_items": ["herb"],
        })
        battle._apply_battle_victory(player, state)

        names = [i["name"] for i in player["inventory"]]
        assert "🍎 Смоляное яблоко" in names
        assert "🌿 Трава" in names
        assert player["xp"] == 15  # uncommon
        assert player["wins"] == 1

    def test_regular_mob_pool_only(self, tmp_path, monkeypatch):
        monkeypatch.setattr(battle, "random", FakeRandom(0.99))  # 50% дроп не сработал
        data_path = self._prepare(tmp_path, monkeypatch)
        player = self._player(data_path)

        state = _battle(enemy={
            "name": "Крыс", "tier": "common", "win_hits": 1, "dmg_per_hit": 25,
            "base_reward": 0, "loot": [], "pool_items": ["herb"],
        })
        battle._apply_battle_victory(player, state)

        assert player["inventory"] == []
        assert player["xp"] == 10  # common


class FakeRandom:
    """Детерминированная замена random: 0.0 / первый элемент / минимум.

    seq — очередь значений для random(): пока не исчерпана, каждый вызов
    забирает следующее значение из списка (для проверок каскадных бросков).
    """

    def __init__(self, random_value=0.0, seq=None):
        self.random_value = random_value
        self._seq = list(seq or [])

    def random(self):
        if self._seq:
            return self._seq.pop(0)
        return self.random_value

    def choice(self, seq):
        return seq[0]

    def randint(self, a, b):
        return a

    def uniform(self, a, b):
        return a


class TestPetPerks:
    """Перки питомцев в боевой системе"""

    def _state(self, perks, player=None):
        state = _battle()
        state["pet"] = {"name": "🦀 Краб", "perks": perks}
        return state, (player or {"hp": 200, "max_hp": 200, "xp": 0, "inventory": []})

    def test_shield_blocks_first_counter_only(self):
        state, player = self._state({"shield": True})
        state["player_attack"] = "body"   # == enemy_block "body" -> промах
        state["player_block"] = "body"    # != enemy_attack "head" -> урон, но панцирь

        outcome = battle._resolve_round(state, player)
        assert outcome == "continue"
        assert state["dmg_taken"] == 0            # панцирь принял удар
        assert state["journal"][0]["pet_save"] == "🦀 Краб"

        # Второй неверный блок уже платит по тарифу (зоны фиксируем руками).
        battle._start_round(state)
        state["enemy_attack"] = "head"
        state["enemy_block"] = "head"
        state["player_attack"] = "head"           # промах
        state["player_block"] = "body"
        battle._resolve_round(state, player)
        assert state["dmg_taken"] == 25
        assert state["journal"][1].get("pet_save") is None

    def test_scavenger_fills_failed_pool_drop(self, tmp_path, monkeypatch):
        _setup_paths(tmp_path, monkeypatch)
        # 0.9: пул не сработал; 0.3: докидка (royal 0.4) сработала.
        monkeypatch.setattr(battle, "random", FakeRandom(seq=[0.9, 0.3]))
        player = {"hp": 200, "max_hp": 200, "xp": 0, "inventory": []}
        enemy = {"name": "Крыс", "tier": "common", "win_hits": 1, "dmg_per_hit": 25,
                 "base_reward": 0, "loot": [], "pool_items": ["herb"]}
        state = _battle(enemy=enemy,
                        pet={"name": "👑 Королевский хуекраб", "perks": {"scavenger": 0.4}})
        state["player_attack"] = "head"
        state["player_block"] = "head"  # враг держит "head" -> добивающее попадание 1/1

        assert battle._resolve_round(state, player) == "victory"
        rewards = battle._settle_battle(player, state, "victory")
        names = [i["name"] for i in player["inventory"]]
        assert names == ["🌿 Трава"]  # только крабова докидка
        assert rewards["xp"] == 10

    def test_berserk_boosts_drop_chance(self, tmp_path, monkeypatch):
        _setup_paths(tmp_path, monkeypatch)
        monkeypatch.setattr(battle, "random", FakeRandom(0.6))  # 0.6: базовый 50% мимо, ярость 75% попала
        player = {"hp": 200, "max_hp": 200, "xp": 0, "inventory": []}
        enemy = {"name": "Крыс", "tier": "common", "win_hits": 1, "dmg_per_hit": 25,
                 "base_reward": 0, "loot": [], "pool_items": ["herb"]}
        state = _battle(enemy=enemy, pet={"name": "🦀 Краб", "perks": {"berserk": 0.75}})

        battle._apply_battle_victory(player, state)
        assert [i["name"] for i in player["inventory"]] == ["🌿 Трава"]

    def test_greedy_raises_defeat_loot_chance(self, tmp_path, monkeypatch):
        _setup_paths(tmp_path, monkeypatch)
        monkeypatch.setattr(battle, "random", FakeRandom(0.5))  # 0.5: базовых 25% нет, жадных 60% есть
        player = {"hp": 200, "max_hp": 200, "xp": 0, "inventory": []}
        state = _battle(pet={"name": "👑 Краб", "perks": {"greedy": 0.6}})

        battle._apply_battle_defeat(player, state)
        assert [i["name"] for i in player["inventory"]] == ["🌿 Хвоя"]

    def test_victory_report_credits_scavenger_and_pet_xp(self, tmp_path, monkeypatch):
        """Докидка лута и XP краба видны в отчёте о победе."""
        _setup_paths(tmp_path, monkeypatch)
        # 0.9: пул мимо; 0.2: докидка Лутовика (25%) сработала.
        monkeypatch.setattr(battle, "random", FakeRandom(seq=[0.9, 0.2]))
        player = {"hp": 200, "max_hp": 200, "xp": 0, "inventory": []}
        enemy = {"name": "Крыс", "tier": "common", "win_hits": 1, "dmg_per_hit": 25,
                 "base_reward": 0, "loot": [], "pool_items": ["herb"]}
        state = _battle(enemy=enemy, pet={"name": "🦀 Лутовик", "perks": {"scavenger": 0.25}})
        state["player_attack"] = "head"
        state["player_block"] = "head"

        assert battle._resolve_round(state, player) == "victory"
        rewards = battle._settle_battle(player, state, "victory")
        text = battle._victory_report(state, rewards)
        assert "🦀 Лутовик в деле: +10 XP напарнику" in text
        assert "без него руки были бы пустыми" in text

    def test_victory_report_credits_berserk_drop(self, tmp_path, monkeypatch):
        """Повышенный берсерк-дроп отмечается отдельной строкой."""
        _setup_paths(tmp_path, monkeypatch)
        monkeypatch.setattr(battle, "random", FakeRandom(0.6))  # базовая 50% мимо, ярость 75% попала
        player = {"hp": 200, "max_hp": 200, "xp": 0, "inventory": []}
        enemy = {"name": "Крыс", "tier": "common", "win_hits": 1, "dmg_per_hit": 25,
                 "base_reward": 0, "loot": [], "pool_items": ["herb"]}
        state = _battle(enemy=enemy, pet={"name": "🦀 Берсерк", "perks": {"berserk": 0.75}})

        rewards = battle._settle_battle(player, state, "victory")
        text = battle._victory_report(state, rewards)
        assert "🦀 Берсерк в деле: +10 XP напарнику" in text
        assert "дроп щедрее обычного" in text

    def test_defeat_report_credits_greedy_rescue(self, tmp_path, monkeypatch):
        """Поражение: жадный сбор и +5 XP краба показываются в отчёте."""
        _setup_paths(tmp_path, monkeypatch)
        monkeypatch.setattr(battle, "random", FakeRandom(0.5))  # базовых 25% нет, жадных 60% есть
        player = {"hp": 200, "max_hp": 200, "xp": 0, "inventory": []}
        state = _battle(pet={"name": "🦀 Жадина", "perks": {"greedy": 0.6}})

        rewards = battle._settle_battle(player, state, "defeat")
        text = battle._defeat_report(state, rewards)
        assert "🦀 Жадина тащит побитого гнома в лагерь: +5 XP напарнику" in text
        assert "собрал то, что успел" in text

    def test_scout_hint_only_round_one_attack(self):
        state = _battle(pet={"name": "🦀 Краб", "perks": {"scout": True}})
        state["phase"] = "attack"
        state["round"] = 1

        hint = battle._scout_hint(state)
        assert hint and "держит блок" in hint and "в торс" in hint

        state["round"] = 2
        assert battle._scout_hint(state) is None
        state["round"] = 1
        state["phase"] = "defense"
        assert battle._scout_hint(state) is None

    def test_message_contains_scout_hint_and_alongside(self):
        state = _battle(pet={"name": "🦀 Краб", "perks": {"scout": True}})
        state["phase"] = "attack"
        state["round"] = 1

        # Одно сообщение боя: спутник и подсказка Соглядата вместе с обстановкой.
        text = battle._battle_message_text(state)
        assert "ныряет гномом в бой" in text
        assert "держит блок" in text


class TestPetXp:
    """Хуекраб получает XP за любой бой и апает уровень"""

    def test_pet_xp_on_victory_and_level_up(self, tmp_path, monkeypatch):
        _setup_paths(tmp_path, monkeypatch)
        player = {
            "hp": 200, "max_hp": 200, "xp": 0, "inventory": [],
            "total_expeditions": 0,
            "pet": {"id": "piebald_crab", "name": "🦀 Пёстрый хуекраб",
                    "perks": {}, "level": 1, "xp": 45},
        }
        state = _battle(enemy={"name": "Крыс", "tier": "common", "win_hits": 1,
                               "dmg_per_hit": 25, "base_reward": 0, "loot": [],
                               "pool_items": ["herb"]})
        state["player_attack"] = "head"
        state["player_block"] = "head"  # враг держит "head" -> добивающее попадание

        assert battle._resolve_round(state, player) == "victory"
        rewards = battle._settle_battle(player, state, "victory")

        # 45 + 10 XP за победу = 55 >= 50 -> уровень 2, остаток 5.
        assert rewards["pet_levelled"]["level"] == 2
        assert player["pet"]["level"] == 2
        assert player["pet"]["xp"] == 5

    def test_pet_xp_on_defeat(self, tmp_path, monkeypatch):
        _setup_paths(tmp_path, monkeypatch)
        player = {
            "hp": 200, "max_hp": 200, "xp": 0, "inventory": [],
            "total_expeditions": 0,
            "pet": {"id": "piebald_crab", "name": "🦀 Пёстрый хуекраб",
                    "perks": {}, "level": 1, "xp": 0},
        }
        state = _battle()
        battle._apply_battle_defeat(player, state)
        rewards = battle._settle_battle(player, battle=state, outcome="defeat")
        assert player["pet"]["xp"] == 5  # дежурный опыт за поражение
        assert player["pet"]["level"] == 1
        assert "pet_levelled" not in rewards


class TestBattleWatcher:
    async def test_advances_expired_phase(self, tmp_path, monkeypatch, fake_context):
        monkeypatch.setattr(battle, "random", FakeRandom(0.0))
        state = _battle(phase="attack")
        state["phase_deadline"] = (
            datetime.now(timezone.utc) - timedelta(seconds=5)
        ).isoformat()
        data_path = _setup_paths(tmp_path, monkeypatch)
        _write_player(data_path, {
            "hp": 200, "max_hp": 200, "inventory": [],
            "active_expedition": _expedition(),
            "active_battle": state,
        })

        await battle.battle_watcher_job(fake_context)

        stored = _read_player(data_path)
        assert stored["active_battle"]["phase"] == "defense"
        assert stored["active_battle"]["auto_attack"] is True
        fake_context.bot.send_message.assert_awaited()

    async def test_starts_missed_encounter(self, tmp_path, monkeypatch, fake_context):
        monkeypatch.setattr(battle, "random", FakeRandom(0.0))
        data_path = _setup_paths(tmp_path, monkeypatch)
        _write_player(data_path, {
            "hp": 200, "max_hp": 200, "inventory": [],
            "active_expedition": _expedition(),  # encounter_at в прошлом
        })

        await battle.battle_watcher_job(fake_context)

        stored = _read_player(data_path)
        assert stored["active_battle"] is not None
        fake_context.bot.send_message.assert_awaited_once()


class TestExpeditionWaitsForBattle:
    async def test_completion_defers_while_battle_active(self, tmp_path, monkeypatch, fake_context):
        data_path = _setup_paths(tmp_path, monkeypatch)
        state = _battle()
        state["phase_deadline"] = (
            datetime.now(timezone.utc) + timedelta(seconds=30)
        ).isoformat()
        _write_player(data_path, {
            "hp": 200, "max_hp": 200, "inventory": [],
            "active_expedition": _expedition(minutes_end=-1),  # уже истекла
            "active_battle": state,
        })

        await battle.send_expedition_result(123, None, None, fake_context)

        stored = _read_player(data_path)
        assert stored["active_expedition"] is not None  # ждёт конца боя
        scheduled = [
            call for call in fake_context.job_queue.calls
            if call[0] is adv._expedition_complete_callback
        ]
        assert scheduled
        fake_context.bot.send_message.assert_not_awaited()


class TestBattleReports:
    def test_message_contains_player(self):
        text = battle._battle_message_text(_battle(username="tester"))
        assert "tester против Куст" in text

    def test_message_mentions_player_once(self):
        text = battle._battle_message_text(_battle(username="tester"))
        assert "tester" in text
        assert text.count("tester") == 1  # юзернейм без дублирования

    def test_victory_report_contains_player(self):
        state = _battle(username="tester", hits=1)
        state["enemy"]["win_hits"] = 1
        state["journal"] = [
            {"round": 1, "attack": "body", "block": "head", "auto": False,
             "hit": True, "blocked": None, "dmg": 0}
        ]
        rewards = {"xp": 10, "items": [], "healed": 0, "leveled_up": False, "level": 1, "max_hp": 200}
        text = battle._victory_report(state, rewards)
        assert "tester" in text
        assert "ПОБЕДА" in text
        assert "Итоги боя" in text
        assert "Хроника боя" in text
        assert "Куст" in text

    def test_victory_report_has_empty_line_between_sections(self):
        state = _battle(username="tester", hits=1)
        state["enemy"]["win_hits"] = 1
        state["journal"] = [
            {"round": 1, "attack": "body", "block": "head", "auto": False,
             "hit": True, "blocked": None, "dmg": 0}
        ]
        rewards = {"xp": 10, "items": [], "healed": 0, "leveled_up": False, "level": 1, "max_hp": 200}
        text = battle._victory_report(state, rewards)
        # Секции разнесены пустыми строками (duel-style форматирование)
        assert "\n\n" in text
        assert "\n\n\n" not in text

    def test_defeat_report_contains_player(self):
        text = battle._defeat_report(_battle(username="tester"), {"items": []})
        assert "tester" in text
        assert "ГНОМ ПОВЕРЖЕН" in text
        assert "Итоги боя" in text
        assert "Хроника боя" in text

    def test_round_art_hit_with_counter(self):
        state = _battle(username="tester")
        state["journal"] = [
            {"round": 1, "attack": "head", "block": "body", "auto": False,
             "hit": True, "blocked": False, "dmg": 25,
             "enemy_attack": "body", "enemy_block": "head"}
        ]
        text = battle._round_art(state, entry=state["journal"][0])
        assert "tester" in text
        assert "в голову" in text            # зона атаки
        assert "в торс" in text              # ответка врага
        assert "25" in text                  # урон
        assert "\n" in text                  # атака и ответка — на разных строках

    def test_round_art_win_hit_cancels_counter(self):
        state = _battle(username="tester")
        state["journal"] = [
            {"round": 2, "attack": "dick", "block": "head", "auto": False,
             "hit": True, "blocked": None, "dmg": 0,
             "enemy_attack": "head", "enemy_block": "body"}
        ]
        text = battle._round_art(state, final=True, entry=state["journal"][0])
        assert "🔥" in text                  # каталог финального удара
        assert "в хуй" in text
        assert "\n" not in text              # победный хит гасит ответку — одна строка

    def test_message_contains_last_round_art(self):
        state = _battle(username="tester")
        state["journal"] = [
            {"round": 1, "attack": "head", "block": "body", "auto": False,
             "hit": False, "blocked": False, "dmg": 25,
             "enemy_attack": "body", "enemy_block": "head"}
        ]
        text = battle._battle_message_text(state)
        assert "tester против Куст" in text
        assert "в голову" in text            # арт прошлого раунда в фазовом сообщении

    def test_username_fallback(self):
        state = _battle()
        state["username"] = None
        assert battle._battle_username(state) == "Гном"


class TestBattleCallback:
    async def test_zone_choice_advances_phase(self, tmp_path, monkeypatch, fake_context):
        monkeypatch.setattr(battle, "random", FakeRandom(0.0))
        data_path = _setup_paths(tmp_path, monkeypatch)
        _write_player(data_path, {
            "hp": 200, "max_hp": 200, "lvl": 1,
            "active_expedition": _expedition(),
        })
        state = battle.try_start_battle(123)
        assert state is not None

        query = SimpleNamespace(data=f"advb_{state['battle_id']}_atk_body", answer=AsyncMock())
        update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=123))

        await battle.battle_callback(update, fake_context)

        query.answer.assert_awaited_once()
        stored = _read_player(data_path)
        assert stored["active_battle"]["phase"] == "defense"
        assert stored["active_battle"]["player_attack"] == "body"

    async def test_stale_answers_with_text(self, tmp_path, monkeypatch, fake_context):
        _setup_paths(tmp_path, monkeypatch)
        query = SimpleNamespace(data="advb_dead0_atk_head", answer=AsyncMock())
        update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=123))

        await battle.battle_callback(update, fake_context)

        query.answer.assert_awaited_once_with("Этот бой уже окончен.")
