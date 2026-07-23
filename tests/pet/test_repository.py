from __future__ import annotations


class TestFindOrCreate:
    def test_first_access_creates_with_table_defaults(self, pet_repo):
        pet = pet_repo.find_or_create()
        assert pet.player_id == "default"
        assert pet.mood == "normal"
        assert pet.hunger == 80
        assert pet.equipped_hat is None

    def test_repeated_call_is_idempotent(self, pet_repo):
        a = pet_repo.find_or_create()
        b = pet_repo.find_or_create()
        assert a.player_id == b.player_id


class TestUpdates:
    def test_update_mood_persists(self, pet_repo):
        pet_repo.update_mood("happy")
        assert pet_repo.find_or_create().mood == "happy"

    def test_update_hunger_persists(self, pet_repo):
        pet_repo.update_hunger(42)
        assert pet_repo.find_or_create().hunger == 42

    def test_update_equipped_hat_persists(self, pet_repo):
        pet_repo.update_equipped_hat("row-abc")
        assert pet_repo.find_or_create().equipped_hat == "row-abc"

    def test_equipped_hat_can_be_cleared_to_none(self, pet_repo):
        pet_repo.update_equipped_hat("row-abc")
        pet_repo.update_equipped_hat(None)
        assert pet_repo.find_or_create().equipped_hat is None
