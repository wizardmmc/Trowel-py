"""
sign_in handler tests — streak boundaries (first / consecutive / cap / broken).
bonus formula: xp = 20 + min((streak - 1) * 5, 50).
"""
from __future__ import annotations

from datetime import timedelta

from trowel_py.events.handlers.sign_in import SignInHandler
from trowel_py.events.types import GameState
from trowel_py.player.repository import create_player_repository

from tests.events.conftest import NOW, make_deps


def _state() -> GameState:
    return GameState(total_cards=0, due_cards=0, player_level=1, streak_days=0)


def _seed_streak(db, streak_days: int, last_active) -> None:
    create_player_repository(db).update_streak(streak_days, last_active)


class TestSignInXp:
    def test_first_sign_in(self, db):
        _seed_streak(db, 0, NOW - timedelta(days=1))  # yesterday, streak 0
        result = SignInHandler().execute(_state(), make_deps(db, now=NOW))
        assert result.xp == 20  # 20 + min(0, 50)
        assert "第 1 天" in result.description

    def test_consecutive_day(self, db):
        _seed_streak(db, 1, NOW - timedelta(days=1))
        result = SignInHandler().execute(_state(), make_deps(db, now=NOW))
        assert result.xp == 25  # 20 + min(5, 50)
        assert "第 2 天" in result.description

    def test_bonus_caps_at_50(self, db):
        # streak 10 yesterday -> 11 today -> bonus = min(50, 50) -> xp 70
        _seed_streak(db, 10, NOW - timedelta(days=1))
        result = SignInHandler().execute(_state(), make_deps(db, now=NOW))
        assert result.xp == 70

    def test_broken_streak_resets(self, db):
        _seed_streak(db, 5, NOW - timedelta(days=3))  # 3-day gap -> reset
        result = SignInHandler().execute(_state(), make_deps(db, now=NOW))
        assert result.xp == 20
        assert "第 1 天" in result.description

    def test_streak_persisted_to_db(self, db):
        _seed_streak(db, 1, NOW - timedelta(days=1))
        SignInHandler().execute(_state(), make_deps(db, now=NOW))
        assert create_player_repository(db).find_or_create().streak_days == 2
