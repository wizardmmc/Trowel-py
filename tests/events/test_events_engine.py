"""tests for the event engine: weight adjustment, deterministic weighted
random (randomness removed), and statistical distribution over many draws
(randomness accepted). select_event's tests double as this slice's end-to-end
pipeline check, since there is no HTTP/DB/service layer here.
"""
from __future__ import annotations

import random
from collections import Counter
from datetime import datetime

import pytest

from trowel_py.events.engine import _adjust_weights, _weighted_random, select_event
from trowel_py.events.types import EventConfig, GameState

NOW = datetime(2026, 6, 16, 12, 0, 0)


def _cfg(type_: str, weight: int, min_cards: int = 0) -> EventConfig:
    """small helper: cooldown fixed at 60 min, only the interesting fields vary."""
    return EventConfig(
        type=type_, weight=weight, cooldown_minutes=60, min_cards=min_cards
    )


def _state(total_cards: int = 0) -> GameState:
    return GameState(
        total_cards=total_cards,
        due_cards=0,
        player_level=1,
        streak_days=0,
        learned_card_ids=(),
    )


# --- _adjust_weights: dynamic weighting ---


class TestAdjustWeights:
    def test_non_challenge_keeps_base_weight(self):
        weighted = _adjust_weights((_cfg("gift", 15),), _state(total_cards=100))
        # assert the weight value directly; comparing the whole tuple risks the
        # single-element-tuple trap (a trailing comma is easy to miss).
        assert len(weighted) == 1
        assert weighted[0][1] == 15.0

    def test_challenge_doubles_at_100_cards(self):
        # weight * (1 + 100/100) = 40 * 2 = 80
        weighted = _adjust_weights((_cfg("challenge", 40),), _state(total_cards=100))
        assert weighted[0][1] == pytest.approx(80.0)

    def test_challenge_no_boost_at_zero_cards(self):
        weighted = _adjust_weights((_cfg("challenge", 40),), _state(total_cards=0))
        assert weighted[0][1] == pytest.approx(40.0)


# --- _weighted_random: deterministic (randomness removed via injected rand) ---


class TestWeightedRandomDeterministic:
    def _items(self):
        # a=1, b=2, c=3 -> total 6; buckets [0,1) [1,3) [3,6)
        return (
            (_cfg("a", 1), 1.0),
            (_cfg("b", 2), 2.0),
            (_cfg("c", 3), 3.0),
        )

    def test_rand_zero_picks_first(self):
        # 0 * 6 = 0 -> -1 = -1 <= 0 -> a
        assert _weighted_random(self._items(), rand=0.0) == "a"

    def test_rand_in_first_bucket_picks_first(self):
        # 0.16 * 6 = 0.96 < 1 -> a
        assert _weighted_random(self._items(), rand=0.16) == "a"

    def test_rand_in_second_bucket_picks_second(self):
        # 0.3 * 6 = 1.8 -> -1=0.8 -> -2=-1.2 -> b
        assert _weighted_random(self._items(), rand=0.3) == "b"

    def test_rand_in_last_bucket_picks_last(self):
        # 0.9 * 6 = 5.4 -> -1=4.4 -> -2=2.4 -> -3=-0.6 -> c
        assert _weighted_random(self._items(), rand=0.9) == "c"

    def test_exact_bucket_boundary_falls_to_earlier(self):
        # rand=1/6 -> remaining=1.0 -> -1=0.0 <= 0 -> a (boundary belongs earlier)
        assert _weighted_random(self._items(), rand=1 / 6) == "a"


# --- select_event: the pipeline (this slice's "end-to-end") ---


class TestSelectEventPipeline:
    def test_no_eligible_returns_none(self):
        # min_cards=3 but total_cards=0 -> filtered out -> None
        configs = (_cfg("challenge", 40, min_cards=3),)
        assert select_event(_state(total_cards=0), configs, {}, NOW) is None

    def test_cooldown_blocks_the_only_event_returns_none(self):
        configs = (_cfg("gift", 15),)
        cooldowns = {"gift": NOW}
        assert select_event(_state(total_cards=10), configs, cooldowns, NOW) is None

    def test_single_eligible_always_returns_it(self):
        # only one eligible -> no matter the rand, it must win
        configs = (_cfg("gift", 15),)
        for seed in range(20):
            rng = random.Random(seed)
            assert select_event(_state(total_cards=10), configs, {}, NOW, rng=rng) == "gift"


# --- select_event: statistical distribution (randomness accepted) ---


class TestSelectEventDistribution:
    def test_frequency_matches_weight_ratio(self):
        # two events weight 1:3 -> expect ~25% / ~75% over many draws
        configs = (_cfg("a", 1), _cfg("b", 3))
        rng = random.Random(42)
        counts = Counter(
            select_event(_state(total_cards=0), configs, {}, NOW, rng=rng)
            for _ in range(10000)
        )
        total = sum(counts.values())
        # statistical, so allow tolerance rather than exact equality
        assert counts["a"] / total == pytest.approx(0.25, abs=0.03)
        assert counts["b"] / total == pytest.approx(0.75, abs=0.03)

    def test_challenge_boost_changes_distribution(self):
        # at 100 cards challenge weight doubles (40 -> 80) vs gift (15).
        # share = 80 / (80 + 15) = 84.2%, vs unboosted 40 / 55 = 72.7%.
        configs = (_cfg("challenge", 40), _cfg("gift", 15))
        rng = random.Random(42)
        counts = Counter(
            select_event(_state(total_cards=100), configs, {}, NOW, rng=rng)
            for _ in range(10000)
        )
        total = sum(counts.values())
        assert counts["challenge"] / total == pytest.approx(80 / 95, abs=0.03)
