from __future__ import annotations

import random
from unittest.mock import MagicMock, call

import pytest

from trowel_py.pet.brain import TemplateBrain
from trowel_py.pet.repository import PetRepository
from trowel_py.pet.service import (
    HUNGER_MAX,
    _clamp_hunger,
    equip_hat,
    feed,
    interact,
    resolve_mood,
    tick_hunger,
    update_mood,
)
from trowel_py.player.repository import PlayerRepository
from trowel_py.schemas.pet import Pet
from trowel_py.schemas.player import InventoryItem


class TestResolveMood:
    @pytest.mark.parametrize(
        "trigger,mood",
        [
            ("review_correct", "happy"),
            ("review_complete", "excited"),
            ("event_trigger", "excited"),
            ("interaction", "happy"),
            ("hunger_low", "normal"),
            ("idle", "normal"),
            ("feynman_trigger", "curious"),
        ],
    )
    def test_transition_table(self, trigger, mood):
        assert resolve_mood(trigger) == mood


class TestClampHunger:
    def test_negative_clamps_to_zero(self):
        assert _clamp_hunger(-5) == 0

    def test_above_max_clamps_to_max(self):
        assert _clamp_hunger(150) == HUNGER_MAX

    def test_in_range_passes_through(self):
        assert _clamp_hunger(42) == 42


class TestFeed:
    def test_restores_hunger_and_consumes_food(self, pet_repo, player_repo, stock_item):
        food_id = stock_item("food_basic", "food")
        pet_repo.update_hunger(50)
        pet = feed(food_id, pet_repo, player_repo)
        assert pet.hunger == 70
        assert player_repo.find_inventory() == []

    def test_clamps_at_max(self, pet_repo, player_repo, stock_item):
        food_id = stock_item("food_premium", "food")
        pet = feed(food_id, pet_repo, player_repo)
        assert pet.hunger == HUNGER_MAX

    def test_missing_item_raises(self, pet_repo, player_repo):
        with pytest.raises(ValueError, match="not in inventory"):
            feed("does-not-exist", pet_repo, player_repo)

    def test_non_food_raises(self, pet_repo, player_repo, stock_item):
        hat_id = stock_item("hat_straw", "hat")
        with pytest.raises(ValueError, match="not food"):
            feed(hat_id, pet_repo, player_repo)

    def test_unknown_food_catalog_raises(self, pet_repo, player_repo, stock_item):
        mystery_id = stock_item("food_mystery", "food")
        with pytest.raises(ValueError, match="unknown food"):
            feed(mystery_id, pet_repo, player_repo)

    def test_preserves_inventory_and_pet_write_order(self):
        pet_repo = MagicMock(spec=PetRepository)
        pet_repo.find_or_create.side_effect = [
            Pet(player_id="default", hunger=50),
            Pet(player_id="default", hunger=70),
        ]
        player_repo = MagicMock(spec=PlayerRepository)
        player_repo.find_item_by_id.return_value = InventoryItem(
            id="inventory-food",
            player_id="default",
            item_id="food_basic",
            item_type="food",
        )
        calls = MagicMock()
        calls.attach_mock(player_repo, "player")
        calls.attach_mock(pet_repo, "pet")

        result = feed("inventory-food", pet_repo, player_repo)

        assert result.hunger == 70
        assert calls.mock_calls == [
            call.player.find_item_by_id("inventory-food"),
            call.pet.find_or_create(),
            call.player.remove_item("inventory-food"),
            call.pet.update_hunger(70),
            call.pet.find_or_create(),
        ]


class TestEquipHat:
    def test_equips_and_syncs_pet(self, pet_repo, player_repo, stock_item):
        hat_id = stock_item("hat_straw", "hat")
        pet = equip_hat(hat_id, pet_repo, player_repo)
        assert pet.equipped_hat == hat_id
        assert player_repo.find_item_by_id(hat_id).equipped == 1

    def test_second_hat_unequips_first(self, pet_repo, player_repo, stock_item):
        hat_a = stock_item("hat_straw", "hat")
        hat_b = stock_item("hat_cap", "hat")
        equip_hat(hat_a, pet_repo, player_repo)
        equip_hat(hat_b, pet_repo, player_repo)

        assert player_repo.find_item_by_id(hat_a).equipped == 0
        assert player_repo.find_item_by_id(hat_b).equipped == 1
        assert pet_repo.find_or_create().equipped_hat == hat_b

    def test_missing_item_raises(self, pet_repo, player_repo):
        with pytest.raises(ValueError, match="not in inventory"):
            equip_hat("nope", pet_repo, player_repo)

    def test_non_hat_raises(self, pet_repo, player_repo, stock_item):
        food_id = stock_item("food_basic", "food")
        with pytest.raises(ValueError, match="not a hat"):
            equip_hat(food_id, pet_repo, player_repo)

    def test_preserves_inventory_and_pet_write_order(self):
        pet_repo = MagicMock(spec=PetRepository)
        pet_repo.find_or_create.return_value = Pet(
            player_id="default",
            equipped_hat="inventory-hat",
        )
        player_repo = MagicMock(spec=PlayerRepository)
        player_repo.find_item_by_id.return_value = InventoryItem(
            id="inventory-hat",
            player_id="default",
            item_id="hat_straw",
            item_type="hat",
        )
        calls = MagicMock()
        calls.attach_mock(player_repo, "player")
        calls.attach_mock(pet_repo, "pet")

        result = equip_hat("inventory-hat", pet_repo, player_repo)

        assert result.equipped_hat == "inventory-hat"
        assert calls.mock_calls == [
            call.player.find_item_by_id("inventory-hat"),
            call.player.unequip_all_hats(),
            call.player.set_equipped("inventory-hat", 1),
            call.pet.update_equipped_hat("inventory-hat"),
            call.pet.find_or_create(),
        ]


class TestInteract:
    def test_sets_happy_and_returns_a_line(self, pet_repo):
        pet_repo.update_mood("normal")
        result = interact(pet_repo, TemplateBrain(), random.Random(0))
        response = result["response"]
        assert response.mood == "happy"
        assert response.text
        assert result["pet"].mood == "happy"


class TestTickHunger:
    def test_reduces_hunger_by_elapsed(self, pet_repo):
        pet_repo.update_hunger(80)
        pet = tick_hunger(pet_repo, elapsed_minutes=60)
        assert pet.hunger == 78

    def test_clamps_at_zero(self, pet_repo):
        pet_repo.update_hunger(1)
        pet = tick_hunger(pet_repo, elapsed_minutes=60)
        assert pet.hunger == 0


class TestUpdateMood:
    def test_persists_transition(self, pet_repo):
        update_mood("review_correct", pet_repo)
        assert pet_repo.find_or_create().mood == "happy"
