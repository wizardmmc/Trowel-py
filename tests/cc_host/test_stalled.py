"""Tests for cc_host.stalled.StalledDetector.

Stalled = "no CC event for T seconds AND not currently inside an api_retry
backoff wait". Two thresholds: normal (default phase) vs generating (last event
was a thinking_tokens heartbeat — GLM silently producing a big tool_use). Pure
logic — time is injected so we can test boundary cases without sleeping.
"""
import pytest

from trowel_py.cc_host.stalled import StalledDetector


class TestStalledDetection:
    """Normal-phase detection (last event NOT thinking_tokens)."""

    def test_not_stalled_before_normal_threshold(self):
        d = StalledDetector(threshold_normal=100.0, threshold_generating=500.0)
        d.start_turn(now=0.0)
        d.record_event(now=0.0)
        assert d.is_stalled(now=99.0) is False

    def test_stalled_at_or_after_normal_threshold(self):
        d = StalledDetector(threshold_normal=100.0, threshold_generating=500.0)
        d.start_turn(now=0.0)
        d.record_event(now=0.0)
        assert d.is_stalled(now=100.0) is True
        assert d.is_stalled(now=150.0) is True

    def test_any_event_resets_the_quiet_clock(self):
        d = StalledDetector(threshold_normal=100.0, threshold_generating=500.0)
        d.start_turn(now=0.0)
        d.record_event(now=0.0)
        d.record_event(now=80.0)  # any event keeps it alive
        assert d.is_stalled(now=120.0) is False  # only 40s since last event
        assert d.is_stalled(now=180.0) is True

    def test_not_stalled_while_in_retry_backoff(self):
        d = StalledDetector(threshold_normal=100.0, threshold_generating=500.0)
        d.start_turn(now=0.0)
        d.record_event(now=0.0)
        # CC says it is retrying with a long backoff; even past the threshold,
        # being inside the backoff window means "waiting, not dead".
        d.record_retry(now=10.0, retry_delay_ms=240_000)  # retry_until = 250
        assert d.is_stalled(now=200.0) is False  # inside backoff (200 < 250)
        assert d.is_stalled(now=260.0) is True  # backoff expired + past threshold

    def test_not_started_yet_is_not_stalled(self):
        d = StalledDetector(threshold_normal=100.0, threshold_generating=500.0)
        assert d.is_stalled(now=1000.0) is False


class TestGeneratingPhase:
    """Generating-phase detection (last event IS a thinking_tokens heartbeat).

    GLM's non-streaming backend is silent while producing a large tool_use; the
    last event before that silence is a system/thinking_tokens heartbeat (cc
    reports "I just thought; output not landed yet"). The detector must use the
    larger threshold so legitimate slow generation (measured up to ~670s on
    7/2–7/5) isn't misfired as a stall.
    """

    def test_generating_phase_uses_larger_threshold(self):
        d = StalledDetector(threshold_normal=100.0, threshold_generating=500.0)
        d.start_turn(now=0.0)
        d.record_event(now=0.0, is_generating=True)  # thinking_tokens heartbeat
        # 150s: past normal threshold, but in generating phase → not stalled
        assert d.is_stalled(now=150.0) is False
        assert d.is_stalled(now=499.0) is False
        assert d.is_stalled(now=500.0) is True

    def test_non_thinking_event_switches_back_to_normal(self):
        """An assistant envelope (or any non-thinking event) ends generating phase."""
        d = StalledDetector(threshold_normal=100.0, threshold_generating=500.0)
        d.start_turn(now=0.0)
        d.record_event(now=0.0, is_generating=True)  # thinking
        d.record_event(now=50.0, is_generating=False)  # assistant envelope arrived
        # now the normal threshold applies again (last event was non-thinking)
        assert d.is_stalled(now=149.0) is False  # 99s since last event
        assert d.is_stalled(now=150.0) is True  # 100s

    def test_start_turn_resets_phase(self):
        d = StalledDetector(threshold_normal=100.0, threshold_generating=500.0)
        d.start_turn(now=0.0)
        d.record_event(now=0.0, is_generating=True)
        d.start_turn(now=10.0)  # new user message
        assert d.in_generating_phase() is False
        # normal threshold applies on a fresh turn
        assert d.is_stalled(now=109.0) is False
        assert d.is_stalled(now=110.0) is True

    def test_in_generating_phase_reflects_last_event(self):
        d = StalledDetector(threshold_normal=100.0, threshold_generating=500.0)
        d.start_turn(now=0.0)
        assert d.in_generating_phase() is False
        d.record_event(now=10.0, is_generating=True)
        assert d.in_generating_phase() is True
        d.record_event(now=20.0, is_generating=False)
        assert d.in_generating_phase() is False


class TestRestartBudget:
    def test_can_restart_once_per_turn(self):
        d = StalledDetector(threshold_normal=100.0, max_restarts_per_turn=1)
        d.start_turn(now=0.0)
        assert d.can_restart() is True
        d.note_restart()
        assert d.can_restart() is False

    def test_restart_budget_resets_each_turn(self):
        d = StalledDetector(threshold_normal=100.0, max_restarts_per_turn=1)
        d.start_turn(now=0.0)
        d.note_restart()
        assert d.can_restart() is False
        d.start_turn(now=100.0)  # new user message
        assert d.can_restart() is True

    def test_when_budget_exhausted_is_stalled_is_still_true_but_service_errors(self):
        # the detector keeps reporting stalled; the service is what decides to
        # translate the second one into an error instead of another restart.
        d = StalledDetector(threshold_normal=100.0, max_restarts_per_turn=1)
        d.start_turn(now=0.0)
        d.record_event(now=0.0)
        d.note_restart()
        assert d.can_restart() is False
        assert d.is_stalled(now=300.0) is True
