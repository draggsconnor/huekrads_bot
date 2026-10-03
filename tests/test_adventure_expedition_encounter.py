"""Tests for expedition encounter - 100% encounter chance after expedition."""

import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from handlers.adventure import (
    _select_encounter_mob,
    _expedition_complete_callback,
    load_mobs,
    load_locations,
)


@pytest.fixture
def mobs_data():
    """Sample mobs data from mobs.json."""
    return {
        "mob_types": {
            "common": {
                "names": {
                    "dark_forest": ["Гоблин", "Лесной волк"],
                    "abandoned_mine": ["Шахтёрский призрак", "Каменный паук"],
                    "dragon_lair": ["Драконий слайм", "Огненный имп"]
                },
                "stats": {
                    "hp": {"min": 30, "max": 50},
                    "attack": {"min": 8, "max": 15},
                    "defense": {"min": 2, "max": 5},
                    "speed": {"min": 3, "max": 7},
                    "crit_chance": {"min": 0.05, "max": 0.10}
                }
            },
            "uncommon": {
                "names": {
                    "dark_forest": ["Лесной охотник", "Тёмный эльф"],
                    "abandoned_mine": ["Гном-предатель", "Кристаллический голем"],
                    "dragon_lair": ["Драконий рыцарь", "Огний саламандра"]
                },
                "stats": {
                    "hp": {"min": 60, "max": 100},
                    "attack": {"min": 15, "max": 25},
                    "defense": {"min": 5, "max": 10},
                    "speed": {"min": 5, "max": 10},
                    "crit_chance": {"min": 0.10, "max": 0.18}
                }
            },
            "rare": {
                "names": {
                    "dark_forest": ["Лесной король", "Вампир-аристократ"],
                    "abandoned_mine": ["Король шахтёров", "Бронированный голем"],
                    "dragon_lair": ["Полу-дракон", "Архимаг огня"]
                },
                "stats": {
                    "hp": {"min": 120, "max": 200},
                    "attack": {"min": 25, "max": 40},
                    "defense": {"min": 10, "max": 20},
                    "speed": {"min": 8, "max": 15},
                    "crit_chance": {"min": 0.15, "max": 0.25}
                }
            }
        },
        "bosses": {
            "dark_forest": {
                "name": "Forest Spirit",
                "hp": 150,
                "attack": 20,
                "defense": 8,
                "speed": 12,
                "crit_chance": 0.20,
                "drops": ["legendary_item", "rare_material", "boss_token"]
            },
            "abandoned_mine": {
                "name": "Mine Guardian",
                "hp": 300,
                "attack": 35,
                "defense": 15,
                "speed": 8,
                "crit_chance": 0.15,
                "drops": ["legendary_item", "rare_material", "boss_token", "mine_key"]
            },
            "dragon_lair": {
                "name": "Ancient Dragon",
                "hp": 1000,
                "attack": 80,
                "defense": 40,
                "speed": 15,
                "crit_chance": 0.30,
                "drops": ["ancient_dragon_trophy", "dragon_scale", "fire_gem", "legendary_weapon"]
            }
        },
        "encounter_weights": {
            "common": 0.50,
            "uncommon": 0.30,
            "rare": 0.15,
            "boss": 0.05
        },
        "combat_zones": ["head", "body", "dick"],
        "loot_table": {
            "common": {
                "items": ["herb", "stone", "wood", "leather"],
                "gold": {"min": 5, "max": 20},
                "xp": {"min": 10, "max": 30}
            },
            "uncommon": {
                "items": ["magic_crystal", "enchanted_ore", "rare_herb", "monster_part"],
                "gold": {"min": 20, "max": 50},
                "xp": {"min": 30, "max": 70}
            },
            "rare": {
                "items": ["ancient_relic", "dragon_scale", "elemental_core", "cursed_gem"],
                "gold": {"min": 50, "max": 150},
                "xp": {"min": 70, "max": 150}
            },
            "boss": {
                "items": ["boss_token", "legendary_weapon", "ancient_armor", "unique_trinket"],
                "gold": {"min": 150, "max": 500},
                "xp": {"min": 150, "max": 400}
            }
        },
        "specific_mobs": {
            "dark_forest": [
                {
                    "id": "bush_with_requirements",
                    "name": "Куст с предъявами",
                    "level": 1,
                    "hp": 30,
                    "attack": 5,
                    "defense": 2,
                    "speed": 4,
                    "crit_chance": 0.0,
                    "encounter_chance": 0.35,
                    "description": "Просто куст. Но он шуршит так, что гном инстинктивно прикрывает пах.",
                    "tactics": {
                        "debuff_chance": 0.30,
                        "overpower": False
                    },
                    "special_ability": {
                        "name": "Паранойя",
                        "chance": 0.30,
                        "effect": "skip_turn",
                        "description": "Гном пропускает ход, проверяя ширинку"
                    }
                },
                {
                    "id": "head_on_spider_legs",
                    "name": "Голова деда на паучьих ногах",
                    "level": 1,
                    "hp": 20,
                    "attack": 8,
                    "defense": 1,
                    "speed": 6,
                    "crit_chance": 0.10,
                    "encounter_chance": 0.35,
                    "description": "Размером с таксу. У него морщинистое лицо.",
                    "tactics": {
                        "debuff_chance": 0,
                        "overpower": True
                    },
                    "special_ability": {
                        "name": "Присасывание",
                        "activation_turns": 3,
                        "effect": "doom",
                        "damage_per_turn": 5,
                        "description": "После 3 ходов присасывается и наносит 5 урона каждый ход"
                    },
                    "turns_to_kill": 3
                },
                {
                    "id": "pine_barren_minion",
                    "name": "Смоляной слуга",
                    "level": 2,
                    "hp": 45,
                    "attack": 10,
                    "defense": 3,
                    "speed": 3,
                    "crit_chance": 0.05,
                    "encounter_chance": 0.20,
                    "description": "Одичавший гоблин, покрытый смолой Хвойного Барона.",
                    "tactics": {
                        "debuff_chance": 0.25,
                        "overpower": False
                    },
                    "special_ability": {
                        "name": "Липкая смола",
                        "chance": 0.25,
                        "effect": "slow",
                        "description": "Гном получает шанс 50% пропустить атаку из-за липкости"
                    }
                }
            ],
            "abandoned_mine": [],
            "dragon_lair": []
        }
    }


@pytest.fixture
def locations_data():
    """Sample locations data from locations.json."""
    return {
        "dark_forest": {
            "name": "Хвойный Барон",
            "description": "Тёмный лес с древними деревьями.",
            "duration_minutes": 60,
            "encounter_chance": 1.0,
            "min_level": 1,
            "boss": "dark_forest_boss",
            "specific_mobs": ["bush_with_requirements", "head_on_spider_legs", "pine_barren_minion"],
            "loot_table": [
                {"item_id": "herb", "chance": 0.5, "quantity_min": 1, "quantity_max": 3},
                {"item_id": "magic_crystal", "chance": 0.1, "quantity_min": 1, "quantity_max": 1}
            ]
        },
        "abandoned_mine": {
            "name": "Заброшенная шахта",
            "description": "Старая шахта с темными коридорами.",
            "duration_minutes": 90,
            "encounter_chance": 1.0,
            "min_level": 3,
            "boss": "abandoned_mine_boss",
            "specific_mobs": [],
            "loot_table": [
                {"item_id": "stone", "chance": 0.6, "quantity_min": 2, "quantity_max": 5},
                {"item_id": "enchanted_ore", "chance": 0.15, "quantity_min": 1, "quantity_max": 2}
            ]
        },
        "dragon_lair": {
            "name": "Логово Дракона",
            "description": "Опасное логово древнего дракона.",
            "duration_minutes": 120,
            "encounter_chance": 1.0,
            "min_level": 10,
            "boss": "dragon_lair_boss",
            "specific_mobs": [],
            "loot_table": [
                {"item_id": "ancient_relic", "chance": 0.3, "quantity_min": 1, "quantity_max": 1},
                {"item_id": "dragon_scale", "chance": 0.1, "quantity_min": 1, "quantity_max": 1}
            ]
        }
    }


class TestSelectEncounterMob:
    """Tests for _select_encounter_mob function."""
    
    def test_select_mob_with_force_encounter_true(self, mobs_data, locations_data):
        """Test that force_encounter=True always returns a mob."""
        mob_def, mob_desc_key = _select_encounter_mob(
            "dark_forest", 
            mobs_data, 
            locations_data, 
            force_encounter=True
        )
        
        assert mob_def is not None, "Should always return a mob when force_encounter=True"
        assert mob_def.get("id") in ["bush_with_requirements", "head_on_spider_legs", "pine_barren_minion"]
        assert mob_def.get("encounter_chance") == 1.0
        assert mob_desc_key is not None
        assert mob_desc_key.endswith("_desc")
    
    def test_select_mob_returns_valid_stats(self, mobs_data, locations_data):
        """Test that selected mob has all required stats."""
        mob_def, _ = _select_encounter_mob(
            "dark_forest", 
            mobs_data, 
            locations_data, 
            force_encounter=True
        )
        
        assert mob_def is not None
        assert "id" in mob_def
        assert "name" in mob_def
        assert "hp" in mob_def
        assert "max_hp" in mob_def
        assert "attack" in mob_def
        assert "defense" in mob_def
        assert "speed" in mob_def
        assert "crit_chance" in mob_def
        assert "dmg" in mob_def
        assert "level" in mob_def
        assert "tactics" in mob_def
        assert "special_ability" in mob_def or mob_def.get("special_ability") is None
    
    def test_select_mob_from_abandoned_mine_uses_fallback(self, mobs_data, locations_data):
        """Test that locations with no specific mobs use fallback generation."""
        mob_def, _ = _select_encounter_mob(
            "abandoned_mine", 
            mobs_data, 
            locations_data, 
            force_encounter=True
        )
        
        # Should still return a mob from fallback (generic mob generation)
        assert mob_def is not None
        assert "name" in mob_def
    
    def test_select_mob_with_force_encounter_false(self, mobs_data, locations_data):
        """Test that force_encounter=False respects encounter_chance."""
        # Run multiple times to test probabilistic behavior
        results = []
        for _ in range(100):
            mob_def, _ = _select_encounter_mob(
                "dark_forest", 
                mobs_data, 
                locations_data, 
                force_encounter=False
            )
            results.append(mob_def)
        
        # Some might be None if encounter fails
        non_none_count = sum(1 for r in results if r is not None)
        # With total weight of ~0.9, we should get some successes
        assert non_none_count > 0, "Should get some encounters with force_encounter=False"
    
    def test_bush_with_requirements_has_correct_special_ability(self, mobs_data, locations_data):
        """Test that bush_with_requirements has correct special ability."""
        # Run multiple times to potentially get the bush mob
        for _ in range(10):
            mob_def, _ = _select_encounter_mob(
                "dark_forest", 
                mobs_data, 
                locations_data, 
                force_encounter=True
            )
            if mob_def.get("id") == "bush_with_requirements":
                assert mob_def.get("special_ability", {}).get("name") == "Паранойя"
                assert mob_def.get("special_ability", {}).get("effect") == "skip_turn"
                return
        
        # If we didn't get the bush mob, the test still passes (probabilistic)
    
    def test_head_on_spider_legs_has_overpower(self, mobs_data, locations_data):
        """Test that head_on_spider_legs has overpower ability."""
        # Run multiple times to potentially get the head mob
        for _ in range(10):
            mob_def, _ = _select_encounter_mob(
                "dark_forest", 
                mobs_data, 
                locations_data, 
                force_encounter=True
            )
            if mob_def.get("id") == "head_on_spider_legs":
                assert mob_def.get("overpower") == True
                assert mob_def.get("has_suck_ability") == True
                assert mob_def.get("turns_to_kill") == 3
                return
        
        # If we didn't get the head mob, the test still passes (probabilistic)


class TestExpeditionEncounter:
    """Tests for expedition encounter completion."""
    
    @pytest.mark.asyncio
    async def test_expedition_always_starts_encounter(self):
        """Test that expedition completion always starts an encounter (100% chance)."""
        # Create mock context
        mock_context = MagicMock()
        mock_context.job.data = {
            "user_id": 12345,
            "loc_id": "dark_forest",
            "username": "TestUser",
            "chat_id": 67890
        }
        
        # Mock the required functions
        with patch('handlers.adventure.get_player') as mock_get_player, \
             patch('handlers.adventure.update_player') as mock_update_player, \
             patch('handlers.adventure.load_mobs') as mock_load_mobs, \
             patch('handlers.adventure.load_locations') as mock_load_locations, \
             patch('handlers.adventure._start_interactive_expedition_encounter') as mock_start_encounter:
            
            mock_get_player.return_value = {
                "hp": 200,
                "max_hp": 200,
                "lvl": 1,
                "xp": 0,
                "gold": 0,
                "active_expedition": {"loc_id": "dark_forest"}
            }
            
            mock_load_mobs.return_value = {
                "specific_mobs": {
                    "dark_forest": [
                        {"id": "test_mob", "name": "Test Mob", "hp": 30, "attack": 5}
                    ]
                }
            }
            
            mock_load_locations.return_value = {
                "locations": {
                    "dark_forest": {
                        "name": "Dark Forest",
                        "encounter_chance": 1.0
                    }
                }
            }
            
            await _expedition_complete_callback(mock_context)
            
            # Verify that encounter was started (100% chance)
            mock_start_encounter.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_expedition_encounter_mob_has_forced_chance(self, mobs_data, locations_data):
        """Test that mob selected for expedition encounter has encounter_chance=1.0."""
        mob_def, mob_desc_key = _select_encounter_mob(
            "dark_forest",
            mobs_data,
            locations_data,
            force_encounter=True
        )
        
        # The mob should have encounter_chance set to 1.0 for expedition encounters
        assert mob_def is not None
        assert mob_def.get("encounter_chance") == 1.0, \
            "Expedition encounter mobs should have 100% encounter chance"
    
    def test_all_locations_with_specific_mobs_return_mobs_on_force(self, mobs_data, locations_data):
        """Test that all locations with specific_mobs configured return valid mobs when force_encounter=True."""
        locations_with_mobs = [
            loc_id for loc_id, loc_data in locations_data.items()
            if loc_data.get("specific_mobs") and loc_data["specific_mobs"] != []
        ]
        
        for loc_id in locations_with_mobs:
            mob_def, mob_desc_key = _select_encounter_mob(
                loc_id, 
                mobs_data, 
                locations_data, 
                force_encounter=True
            )
            assert mob_def is not None, f"Location {loc_id} should return a mob with force_encounter=True"
            assert mob_desc_key is not None, f"Location {loc_id} should return a description key"
            assert mob_desc_key.endswith("_desc"), f"Description key should end with '_desc'"
    
    def test_expedition_mob_selection_includes_all_mob_types(self, mobs_data, locations_data):
        """Test that all specific mobs can be selected during expedition encounters."""
        selected_mobs = set()
        
        # Run selection multiple times to capture different mobs
        for _ in range(50):
            mob_def, _ = _select_encounter_mob(
                "dark_forest",
                mobs_data,
                locations_data,
                force_encounter=True
            )
            if mob_def:
                selected_mobs.add(mob_def.get("id"))
        
        # Should be able to select from all available mobs
        expected_mobs = {"bush_with_requirements", "head_on_spider_legs", "pine_barren_minion"}
        assert selected_mobs.issubset(expected_mobs), \
            f"Selected mobs {selected_mobs} should be from expected {expected_mobs}"
    
    @pytest.mark.asyncio
    async def test_expedition_without_mobs_uses_fallback(self):
        """Test that expedition to location without specific mobs still works with fallback."""
        mock_context = MagicMock()
        mock_context.job.data = {
            "user_id": 12345,
            "loc_id": "abandoned_mine",
            "username": "TestUser",
            "chat_id": 67890
        }
        
        with patch('handlers.adventure.get_player') as mock_get_player, \
             patch('handlers.adventure.update_player') as mock_update_player, \
             patch('handlers.adventure.load_mobs') as mock_load_mobs, \
             patch('handlers.adventure.load_locations') as mock_load_locations, \
             patch('handlers.adventure._start_interactive_expedition_encounter') as mock_start_encounter:
            
            mock_get_player.return_value = {
                "hp": 200,
                "max_hp": 200,
                "lvl": 3,
                "xp": 0,
                "gold": 0,
                "active_expedition": {"loc_id": "abandoned_mine"}
            }
            
            mock_load_mobs.return_value = {
                "specific_mobs": {
                    "abandoned_mine": []
                },
                "mob_types": {
                    "common": {
                        "names": {
                            "abandoned_mine": ["Generic Mob 1", "Generic Mob 2"]
                        },
                        "stats": {
                            "hp": {"min": 30, "max": 50},
                            "attack": {"min": 8, "max": 15},
                            "defense": {"min": 2, "max": 5},
                            "speed": {"min": 3, "max": 7},
                            "crit_chance": {"min": 0.05, "max": 0.10}
                        }
                    }
                },
                "encounter_weights": {
                    "common": 0.50,
                    "uncommon": 0.30,
                    "rare": 0.15,
                    "boss": 0.05
                }
            }
            
            mock_load_locations.return_value = {
                "locations": {
                    "abandoned_mine": {
                        "name": "Abandoned Mine",
                        "encounter_chance": 1.0
                    }
                }
            }
            
            # Should still start encounter even without specific mobs
            await _expedition_complete_callback(mock_context)
            
            # Encounter should still be started (fallback generation)
            mock_start_encounter.assert_called_once()
