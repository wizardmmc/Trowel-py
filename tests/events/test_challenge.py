"""
challenge handler tests — each of the three selection paths must be exercised:
  strategy1: due cards preferred
  strategy2: all reviewed -> weighted by unfamiliarity (regression: weight must differ)
  strategy3: recently challenged cards excluded

the min bug hid in strategy2 because nothing tested it; these tests cover it.
"""
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
        seed_state(db, "due_card", reps=2, lapses=0, due=NOW)  # due now
        seed_state(db, "future_card", reps=2, lapses=0)        # far future, not due

        result = ChallengeHandler().execute(_state(), make_deps(db, rng=FakeRng(choice_index=0)))

        assert result.card_id == "due_card"
        assert result.xp == 30
        assert result.coins == 15


class TestStrategy2Weighted:
    def test_picks_unfamiliar_when_rand_high(self, db):
        # both reviewed, neither due -> strategy2
        seed_card(db, "familiar")      # inserted first -> pool[0]
        seed_card(db, "unfamiliar")    # pool[1]
        seed_state(db, "familiar", reps=5, lapses=0)    # weight ~1.0
        seed_state(db, "unfamiliar", reps=1, lapses=2)  # weight ~3.0

        # pool=[familiar(1.0), unfamiliar(3.0)], total=4; rand=0.99 -> 3.96
        # -1.0=2.96 -> -3.0=-0.04 -> unfamiliar
        result = ChallengeHandler().execute(_state(), make_deps(db, rng=FakeRng(rand_value=0.99)))
        assert result.card_id == "unfamiliar"

    def test_picks_familiar_when_rand_zero(self, db):
        seed_card(db, "familiar")
        seed_card(db, "unfamiliar")
        seed_state(db, "familiar", reps=5, lapses=0)
        seed_state(db, "unfamiliar", reps=1, lapses=2)

        # rand=0.0 -> remaining=0 -> first card (familiar)
        result = ChallengeHandler().execute(_state(), make_deps(db, rng=FakeRng(rand_value=0.0)))
        assert result.card_id == "familiar"


class TestStrategy3ExcludeRecent:
    def test_excludes_recently_challenged_card(self, db):
        seed_card(db, "recent")
        seed_card(db, "fresh")
        seed_state(db, "recent", due=NOW)  # both due
        seed_state(db, "fresh", due=NOW)

        ev = create_event_repository(db)
        ev.record_event("challenge", "q", 0, 0, None, "recent", NOW)  # recent in log

        result = ChallengeHandler().execute(_state(), make_deps(db, rng=FakeRng(choice_index=0)))
        assert result.card_id == "fresh"  # "recent" was excluded by strategy3
