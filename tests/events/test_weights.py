"""
pure-function tests for challenge's weight math — no db.

these are the decision core, and the regression guard for the min/max bug:
reps must NOT be flattened to 1 (the old `min(reps, 1)` made every card weight
equal, losing the "fewer reviews = pick it more" signal).
"""
from __future__ import annotations

import random
from collections import Counter
from datetime import datetime

import pytest

from trowel_py.events.handlers.challenge import _unfamiliarity_weight, _weighted_pick
from trowel_py.schemas.card import Card
from trowel_py.schemas.review import FSRSState


def _fsrs(reps: int, lapses: int) -> FSRSState:
    return FSRSState(
        card_id="x",
        stability=1.0,
        difficulty=5.0,
        elapsed_days=1,
        scheduled_days=1,
        reps=reps,
        lapses=lapses,
        state=1,
        due=datetime(2026, 6, 19),
        last_review=datetime(2026, 6, 19),
    )


def _card(cid: str) -> Card:
    return Card(id=cid, title=cid, category="x", explanation="explanation text here")


class TestUnfamiliarityWeight:
    def test_more_lapses_higher_weight(self):
        # same reps, more lapses -> heavier
        assert _unfamiliarity_weight(_fsrs(reps=5, lapses=0)) < _unfamiliarity_weight(
            _fsrs(reps=5, lapses=3)
        )

    def test_fewer_reps_higher_weight(self):
        # same lapses, fewer reps -> heavier. REGRESSION for the min bug:
        # min(reps, 1) would make both 3.0 and this assertion would fail.
        assert _unfamiliarity_weight(_fsrs(reps=5, lapses=2)) < _unfamiliarity_weight(
            _fsrs(reps=1, lapses=2)
        )

    def test_zero_lapses_is_base_weight(self):
        # lapses=0 -> weight = 1 (the +1 base, never zero)
        assert _unfamiliarity_weight(_fsrs(reps=5, lapses=0)) == pytest.approx(1.0)

    def test_zero_reps_no_division_error(self):
        # max(reps, 1) guards division by zero
        assert _unfamiliarity_weight(_fsrs(reps=0, lapses=0)) == pytest.approx(1.0)

    def test_exact_values(self):
        # document the formula: (lapses / reps) + 1
        assert _unfamiliarity_weight(_fsrs(reps=5, lapses=2)) == pytest.approx(1.4)
        assert _unfamiliarity_weight(_fsrs(reps=1, lapses=2)) == pytest.approx(3.0)


class TestWeightedPick:
    def test_same_seed_same_pick(self):
        items = [(_card("light"), 1.0), (_card("heavy"), 9.0)]
        a = _weighted_pick(items, random.Random(0))
        b = _weighted_pick(items, random.Random(0))
        assert a.id == b.id

    def test_zero_rand_picks_first(self, monkeypatch):
        rng = random.Random()
        monkeypatch.setattr(rng, "random", lambda: 0.0)  # remaining = 0 -> first
        items = [(_card("a"), 1.0), (_card("b"), 9.0)]
        assert _weighted_pick(items, rng).id == "a"

    def test_high_rand_picks_heavy(self, monkeypatch):
        rng = random.Random()
        monkeypatch.setattr(rng, "random", lambda: 0.99)  # 9.9 -> -1=8.9 -> -9<0 -> b
        items = [(_card("a"), 1.0), (_card("b"), 9.0)]
        assert _weighted_pick(items, rng).id == "b"

    def test_distribution_favors_heavier(self):
        items = [(_card("light"), 1.0), (_card("heavy"), 9.0)]
        rng = random.Random(42)
        counts = Counter(_weighted_pick(items, rng).id for _ in range(2000))
        assert counts["heavy"] / 2000 == pytest.approx(0.9, abs=0.05)
