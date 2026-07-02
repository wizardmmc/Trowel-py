"""Stalled detection for the CC subprocess.

CC can hang mid-turn (issue #53584: stream-json can deadlock). The host
detects genuine silence — no event for T seconds AND not currently waiting
inside a CC api_retry backoff — and restarts the process once per turn.

This module is a pure state machine; the wall-clock source is injected so the
service can pass `time.monotonic()` and tests can pass deterministic values.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StalledDetector:
    """Track quiet time + retry backoff to decide if CC has stalled.

    Attributes:
        threshold_s: quiet seconds (no event) before stalled. Generous, since
            hard thinking / long tool runs are legitimate; thinking deltas and
            tool_progress events keep the clock alive.
        max_restarts_per_turn: cap on automatic --resume restarts per user
            message (spec: at most 1).
    """

    threshold_s: float = 100.0
    max_restarts_per_turn: int = 1
    _last_event_at: float | None = None
    _retry_until: float | None = None
    _restarts_used: int = 0

    def start_turn(self, now: float) -> None:
        """Begin a new user message: reset the quiet clock and restart budget."""
        self._last_event_at = now
        self._retry_until = None
        self._restarts_used = 0

    def record_event(self, now: float) -> None:
        """Note any CC event arrived; clears an expired retry backoff."""
        self._last_event_at = now
        if self._retry_until is not None and now >= self._retry_until:
            self._retry_until = None

    def record_retry(self, now: float, retry_delay_ms: float) -> None:
        """CC entered an api_retry backoff; don't treat the wait as silence."""
        self._retry_until = now + retry_delay_ms / 1000.0

    def is_stalled(self, now: float) -> bool:
        """True when genuinely silent past the threshold and not backoff-waiting."""
        if self._last_event_at is None:
            return False
        if self._retry_until is not None and now < self._retry_until:
            return False
        return (now - self._last_event_at) >= self.threshold_s

    def can_restart(self) -> bool:
        """Return True if the per-turn restart budget is not yet exhausted."""
        return self._restarts_used < self.max_restarts_per_turn

    def note_restart(self) -> None:
        """Consume one restart from the per-turn budget."""
        self._restarts_used += 1
