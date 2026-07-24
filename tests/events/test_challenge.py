from __future__ import annotations

from trowel_py.events.handlers.challenge import ChallengeHandler
from trowel_py.events.repository import create_event_repository
from trowel_py.events.types import GameState

from tests.events.conftest import NOW, FakeRng, make_deps, seed_card, seed_state


def _state(total: int = 3) -> GameState:
    return GameState(total_cards=total, due_cards=0, player_level=1, streak_days=1)


class TestCanTrigger:
    def test_always_true(self):
        assert ChallengeHandler().can_trigger(_state()) is True


class TestStrategy1DuePreferred:
    def test_picks_due_card_over_future_card(self, db):
        seed_card(db, "due_card")
        seed_card(db, "future_card")
        seed_state(db, "due_card", reps=2, lapses=0, due=NOW)
        seed_state(db, "future_card", reps=2, lapses=0)

        result = ChallengeHandler().execute(
            _state(), make_deps(db, rng=FakeRng(choice_index=0))
        )

        assert result.card_id == "due_card"
        assert result.xp == 30
        assert result.coins == 15


class TestStrategy2Weighted:
    def test_picks_unfamiliar_when_rand_high(self, db):
        seed_card(db, "familiar")
        seed_card(db, "unfamiliar")
        seed_state(db, "familiar", reps=5, lapses=0)
        seed_state(db, "unfamiliar", reps=1, lapses=2)

        result = ChallengeHandler().execute(
            _state(), make_deps(db, rng=FakeRng(rand_value=0.99))
        )
        assert result.card_id == "unfamiliar"

    def test_picks_familiar_when_rand_zero(self, db):
        seed_card(db, "familiar")
        seed_card(db, "unfamiliar")
        seed_state(db, "familiar", reps=5, lapses=0)
        seed_state(db, "unfamiliar", reps=1, lapses=2)

        result = ChallengeHandler().execute(
            _state(), make_deps(db, rng=FakeRng(rand_value=0.0))
        )
        assert result.card_id == "familiar"


class TestStrategy3ExcludeRecent:
    def test_excludes_recently_challenged_card(self, db):
        seed_card(db, "recent")
        seed_card(db, "fresh")
        seed_state(db, "recent", due=NOW)
        seed_state(db, "fresh", due=NOW)

        ev = create_event_repository(db)
        ev.record_event("challenge", "q", 0, 0, None, "recent", NOW)

        result = ChallengeHandler().execute(
            _state(), make_deps(db, rng=FakeRng(choice_index=0))
        )
        assert result.card_id == "fresh"
