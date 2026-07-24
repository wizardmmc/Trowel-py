from __future__ import annotations

from trowel_py.events.handlers.discovery import DiscoveryHandler, _DISCOVERY_ITEMS
from trowel_py.events.handlers.feynman import FeynmanHandler
from trowel_py.events.handlers.gift import GiftHandler, _GIFT_ITEMS
from trowel_py.events.handlers.growth import GrowthHandler
from trowel_py.events.handlers.story import StoryHandler
from trowel_py.events.types import GameState

from tests.events.conftest import FakeRng, make_deps, seed_card, seed_state


def _state(learned=()) -> GameState:
    return GameState(
        total_cards=max(len(learned), 3),
        due_cards=0,
        player_level=1,
        streak_days=0,
        learned_card_ids=learned,
    )


class TestDiscovery:
    def test_can_trigger_true(self):
        assert DiscoveryHandler().can_trigger(_state()) is True

    def test_picks_from_pool(self, db):
        result = DiscoveryHandler().execute(
            _state(), make_deps(db, rng=FakeRng(choice_index=0))
        )
        assert result.event_type == "discovery"
        assert result.xp == 10
        assert result.item_id == _DISCOVERY_ITEMS[0]


class TestGift:
    def test_picks_from_pool(self, db):
        result = GiftHandler().execute(
            _state(), make_deps(db, rng=FakeRng(choice_index=1))
        )
        assert result.event_type == "gift"
        assert result.xp == 10
        assert result.item_id == _GIFT_ITEMS[1]


class TestStory:
    def test_can_trigger_false_when_no_learned(self):
        assert StoryHandler().can_trigger(_state(learned=())) is False

    def test_can_trigger_true_when_learned(self):
        assert StoryHandler().can_trigger(_state(learned=("c1",))) is True

    def test_execute_tells_story_about_card(self, db):
        seed_card(db, "c1", title="Recursion", explanation="a function calling itself")
        result = StoryHandler().execute(
            _state(learned=("c1",)), make_deps(db, rng=FakeRng(choice_index=0))
        )
        assert result.event_type == "story"
        assert result.xp == 5
        assert result.card_id == "c1"
        assert "Recursion" in result.description


class TestGrowth:
    def test_can_trigger_false_when_no_learned(self):
        assert GrowthHandler().can_trigger(_state(learned=())) is False

    def test_execute_reports_growth(self, db):
        seed_card(db, "c1", title="Recursion")
        seed_state(db, "c1", reps=1)
        result = GrowthHandler().execute(
            _state(learned=("c1",)), make_deps(db, rng=FakeRng(choice_index=0))
        )
        assert result.event_type == "growth"
        assert result.xp == 5
        assert result.card_id == "c1"
        assert "Recursion" in result.description


class TestFeynman:
    def test_can_trigger_false(self):
        assert FeynmanHandler().can_trigger(_state()) is False

    def test_execute_returns_placeholder(self, db):
        result = FeynmanHandler().execute(_state(), make_deps(db))
        assert result.xp == 0
        assert result.coins == 0
