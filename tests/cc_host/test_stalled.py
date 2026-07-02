"""Tests for cc_host.stalled.StalledDetector.

Stalled = "no CC event for T seconds AND not currently inside an api_retry
backoff wait". Pure logic — time is injected so we can test boundary cases
without sleeping.
"""
import pytest

from trowel_py.cc_host.stalled import StalledDetector


class TestStalledDetection:
    def test_not_stalled_before_threshold(self):
        d = StalledDetector(threshold_s=100.0)
        d.start_turn(now=0.0)
        d.record_event(now=0.0)
        assert d.is_stalled(now=99.0) is False

    def test_stalled_at_or_after_threshold(self):
        d = StalledDetector(threshold_s=100.0)
        d.start_turn(now=0.0)
        d.record_event(now=0.0)
        assert d.is_stalled(now=100.0) is True
        assert d.is_stalled(now=150.0) is True

    def test_any_event_resets_the_quiet_clock(self):
        d = StalledDetector(threshold_s=100.0)
        d.start_turn(now=0.0)
        d.record_event(now=0.0)
        d.record_event(now=80.0)  # thinking delta / tool_progress keeps it alive
        assert d.is_stalled(now=120.0) is False  # only 40s since last event
        assert d.is_stalled(now=180.0) is True

    def test_not_stalled_while_in_retry_backoff(self):
        d = StalledDetector(threshold_s=100.0)
        d.start_turn(now=0.0)
        d.record_event(now=0.0)
        # CC says it is retrying with a long backoff; even past the threshold,
        # being inside the backoff window means "waiting, not dead".
        d.record_retry(now=10.0, retry_delay_ms=240_000)  # retry_until = 250
        assert d.is_stalled(now=200.0) is False  # inside backoff (200 < 250)
        assert d.is_stalled(now=260.0) is True   # backoff expired + past threshold

    def test_not_started_yet_is_not_stalled(self):
        d = StalledDetector(threshold_s=100.0)
        assert d.is_stalled(now=1000.0) is False


class TestRestartBudget:
    def test_can_restart_once_per_turn(self):
        d = StalledDetector(threshold_s=100.0, max_restarts_per_turn=1)
        d.start_turn(now=0.0)
        assert d.can_restart() is True
        d.note_restart()
        assert d.can_restart() is False

    def test_restart_budget_resets_each_turn(self):
        d = StalledDetector(threshold_s=100.0, max_restarts_per_turn=1)
        d.start_turn(now=0.0)
        d.note_restart()
        assert d.can_restart() is False
        d.start_turn(now=100.0)  # new user message
        assert d.can_restart() is True

    def test_when_budget_exhausted_is_stalled_is_still_true_but_service_errors(self):
        # the detector keeps reporting stalled; the service is what decides to
        # translate the second one into an error instead of another restart.
        d = StalledDetector(threshold_s=100.0, max_restarts_per_turn=1)
        d.start_turn(now=0.0)
        d.record_event(now=0.0)
        d.note_restart()
        assert d.can_restart() is False
        assert d.is_stalled(now=300.0) is True
