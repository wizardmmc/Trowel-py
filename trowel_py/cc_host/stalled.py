"""Stalled detection for the CC subprocess.

CC can hang mid-turn (issue #53584: stream-json can deadlock). The host
detects genuine silence — no event for T seconds AND not currently waiting
inside a CC api_retry backoff — and restarts the process once per turn.

This module is a pure state machine; the wall-clock source is injected so the
service can pass `time.monotonic()` and tests can pass deterministic values.

Two thresholds (slice-029): GLM's non-streaming backend is silent while it
generates a large tool_use — text + tool_use arrive together in one assistant
envelope, with no stream deltas in between. A single threshold is a bad fit:
short → misfires on legitimate slow generation (e.g. a mockup HTML, measured
up to ~670s on 7/2–7/5); long → a real deadlock waits too long. So we pick the
threshold by the phase cc is in, signalled by the **type of the last event**:

- ``threshold_normal`` (default phase): after a tool_result, an assistant
  envelope, etc. Short, so a real wedge is caught fast.
- ``threshold_generating``: when the last event was a ``system/thinking_tokens``
  heartbeat — cc just finished thinking and is now silently generating. Long,
  because that's the one phase where long silence is normal.

Why thinking_tokens is a reliable phase marker (NOT a CPU heuristic): cc emits
it as a real protocol event during thinking (verified by
``spikes/e1-stream-capture.py`` and the [slice025-trace] logs), and generation
itself emits nothing on GLM's backend. So "last event was thinking_tokens" =
"cc reported it just thought; output hasn't landed yet". This is the same kind
of signal as ``_pending_elicit`` (cc telling the host its own state), not an
external heuristic like process-CPU sampling (which can't distinguish
socket-wait from deadlock).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StalledDetector:
    """Track quiet time + retry backoff to decide if CC has stalled.

    Attributes:
        threshold_normal: quiet seconds (no event) before stalled in the
            default phase. Short — catches real wedges fast.
        threshold_generating: quiet seconds before stalled when the last event
            was a thinking_tokens heartbeat (generating phase). Long — covers
            GLM's silent big-tool_use generation (measured max ~670s).
        max_restarts_per_turn: cap on automatic --resume restarts per user
            message (spec: at most 1).
    """

    threshold_normal: float = 120.0
    threshold_generating: float = 720.0
    max_restarts_per_turn: int = 1
    _last_event_at: float | None = None
    _last_event_generating: bool = False
    _retry_until: float | None = None
    _restarts_used: int = 0

    def start_turn(self, now: float) -> None:
        """Begin a new user message: reset the quiet clock and restart budget."""
        self._last_event_at = now
        self._last_event_generating = False
        self._retry_until = None
        self._restarts_used = 0

    def record_event(self, now: float, *, is_generating: bool = False) -> None:
        """Note any CC event arrived; clears an expired retry backoff.

        Args:
            now: monotonic time of the event.
            is_generating: True if this event marks cc as in the generating
                phase (a system/thinking_tokens heartbeat). Selects the larger
                ``threshold_generating`` until a non-thinking event (e.g. the
                assistant envelope) arrives.
        """
        self._last_event_at = now
        self._last_event_generating = is_generating
        if self._retry_until is not None and now >= self._retry_until:
            self._retry_until = None

    def record_retry(self, now: float, retry_delay_ms: float) -> None:
        """CC entered an api_retry backoff; don't treat the wait as silence."""
        self._retry_until = now + retry_delay_ms / 1000.0

    def _threshold(self) -> float:
        """Pick the active threshold from the phase of the last event."""
        return (
            self.threshold_generating
            if self._last_event_generating
            else self.threshold_normal
        )

    def is_stalled(self, now: float) -> bool:
        """True when genuinely silent past the phase's threshold and not backoff-waiting."""
        if self._last_event_at is None:
            return False
        if self._retry_until is not None and now < self._retry_until:
            return False
        return (now - self._last_event_at) >= self._threshold()

    def quiet_seconds(self, now: float) -> float:
        """Return seconds elapsed since the last event (0 if no turn started).

        Used by the service to log how long cc was actually silent before a
        stall fired — distinguishes "barely past threshold" from "wedged long".
        """
        if self._last_event_at is None:
            return 0.0
        return now - self._last_event_at

    def in_generating_phase(self) -> bool:
        """True if the last event was a thinking_tokens (generating-phase) marker.

        Used by the service to log which phase cc was in when a stall fired.
        """
        return self._last_event_generating

    def can_restart(self) -> bool:
        """Return True if the per-turn restart budget is not yet exhausted."""
        return self._restarts_used < self.max_restarts_per_turn

    def note_restart(self) -> None:
        """Consume one restart from the per-turn budget."""
        self._restarts_used += 1
