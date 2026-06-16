"""tests for the cooldown filter: time-window logic and eligibility filtering.

both functions take `now` as an argument, so every test pins the clock to a
fixed NOW instead of waiting or sleeping.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from trowel_py.events.cooldown import filter_eligible, is_on_cooldown
from trowel_py.events.types import EventConfig, GameState

NOW = datetime(2026, 6, 16, 12, 0, 0)


def _state(total_cards: int = 100) -> GameState:
    """a game state with only total_cards varying — enough for min_cards checks."""
    return GameState(
        total_cards=total_cards,
        due_cards=0,
        player_level=1,
        streak_days=0,
        learned_card_ids=(),
    )


# --- is_on_cooldown: the time-window dimensions ---


class TestIsOnCooldown:
    def test_no_record_means_not_on_cooldown(self):
        # never triggered -> free to fire
        assert is_on_cooldown("sign_in", {}, cooldown_minutes=60, now=NOW) is False

    def test_just_triggered_is_on_cooldown(self):
        cooldowns = {"sign_in": NOW}
        assert is_on_cooldown("sign_in", cooldowns, cooldown_minutes=60, now=NOW) is True

    def test_within_window_is_on_cooldown(self):
        # 30 min ago within a 60-min window -> still cooling
        cooldowns = {"sign_in": NOW - timedelta(minutes=30)}
        assert is_on_cooldown("sign_in", cooldowns, cooldown_minutes=60, now=NOW) is True

    def test_past_window_is_off_cooldown(self):
        # 61 min ago past a 60-min window -> free again
        cooldowns = {"sign_in": NOW - timedelta(minutes=61)}
        assert is_on_cooldown("sign_in", cooldowns, cooldown_minutes=60, now=NOW) is False

    def test_boundary_exactly_at_window_is_off_cooldown(self):
        # exactly 60 min ago: the comparison is strict `<`, so equal = expired
        cooldowns = {"sign_in": NOW - timedelta(minutes=60)}
        assert is_on_cooldown("sign_in", cooldowns, cooldown_minutes=60, now=NOW) is False

    def test_other_event_type_does_not_block(self):
        # sign_in being on cooldown must not block challenge
        cooldowns = {"sign_in": NOW}
        assert is_on_cooldown("challenge", cooldowns, cooldown_minutes=60, now=NOW) is False

    def test_clock_skew_future_trigger_is_on_cooldown(self):
        # last_triggered in the future (clock skew / mocked time): elapsed goes
        # negative, which is < window -> currently treated as on cooldown.
        # this test documents that behavior so any future change is intentional.
        cooldowns = {"sign_in": NOW + timedelta(minutes=5)}
        assert is_on_cooldown("sign_in", cooldowns, cooldown_minutes=60, now=NOW) is True


# --- filter_eligible: filtering combinations ---


class TestFilterEligible:
    def _configs(self) -> tuple[EventConfig, ...]:
        return (
            EventConfig(type="sign_in", weight=100, cooldown_minutes=1440, min_cards=0),
            EventConfig(type="challenge", weight=40, cooldown_minutes=60, min_cards=3),
            EventConfig(type="story", weight=15, cooldown_minutes=180, min_cards=5),
        )

    def test_all_eligible_returns_all(self):
        eligible = filter_eligible(self._configs(), _state(total_cards=10), {}, NOW)
        assert [c.type for c in eligible] == ["sign_in", "challenge", "story"]

    def test_min_cards_filters_out_high_threshold_events(self):
        # only 2 cards -> challenge(min 3) and story(min 5) dropped
        eligible = filter_eligible(self._configs(), _state(total_cards=2), {}, NOW)
        assert [c.type for c in eligible] == ["sign_in"]

    def test_all_on_cooldown_returns_empty(self):
        just_now = NOW - timedelta(minutes=1)
        cooldowns = {"sign_in": just_now, "challenge": just_now, "story": just_now}
        eligible = filter_eligible(self._configs(), _state(total_cards=10), cooldowns, NOW)
        assert eligible == ()

    def test_partial_cooldown_keeps_the_rest(self):
        # only challenge on cooldown
        cooldowns = {"challenge": NOW}
        eligible = filter_eligible(self._configs(), _state(total_cards=10), cooldowns, NOW)
        assert [c.type for c in eligible] == ["sign_in", "story"]

    def test_order_is_preserved(self):
        eligible = filter_eligible(self._configs(), _state(total_cards=10), {}, NOW)
        # output keeps input order, not weight order (sign_in weight 100 lists first)
        assert [c.type for c in eligible] == ["sign_in", "challenge", "story"]
