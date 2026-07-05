"""Stalled detection for the CC subprocess — phased heads-up, not auto-restart.

CC can hang mid-turn (issue #53584: stream-json can deadlock). But on GLM's
non-streaming backend, long silence is **usually legitimate waiting**, not a
deadlock: GLM's response latency varies wildly (spike-measured 7s to 160s+ for
the first event after a tool_result), and during that wait cc's stdout is
fully silent. A kill-on-threshold policy misfires on every slow-but-healthy
turn (dc804194: a 162s mockup wait was killed and the restart re-ran grill-me,
losing the user's decisions).

So this module now drives a **phased heads-up** instead of a kill:

- ``threshold_mild`` (120s): emit StalledWarningEvent(severity="mild") — a
  gentle "be patient" under the spinner. Process is left alone.
- ``threshold_severe`` (300s): emit StalledWarningEvent(severity="severe") —
  "may really be stuck". Process still left alone; user interrupts manually.
- ``threshold_kill`` (1800s = 30min): hard cap. Service kills + emits
  ErrorEvent so the turn ends and the session isn't wedged forever (issue
  #53584 backstop for a genuine deadlock).

Why no thinking_tokens-based phase switch (the earlier two-tier design):
spike (spikes/glm-latency-probe.py) showed GLM's "generation" phase is ~0s
(thinking_tokens heartbeats flow right up to the assistant envelope), so the
silent stretch is the **wait for GLM's first event**, which precedes any
thinking_tokens. A thinking_tokens signal arrives only AFTER the stall window
has already fired, so it can't gate the threshold. The wait latency is also
unpredictable enough (max ~670s in 7/2–7/5 measurement) that any single
"normal" threshold misfires; the phased heads-up + 30-min hard cap is the
honest tradeoff.

This module is a pure state machine; the wall-clock source is injected so the
service can pass `time.monotonic()` and tests can pass deterministic values.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StalledDetector:
    """Track quiet time + retry backoff to phase the stall heads-up.

    Attributes:
        threshold_mild: quiet seconds before the mild heads-up fires.
        threshold_severe: quiet seconds before the severe heads-up fires.
        threshold_kill: quiet seconds before the hard cap (service kills cc).
            Generous — only a genuine deadlock (issue #53584) hits this; normal
            GLM waits stay well under it.
    """

    threshold_mild: float = 120.0
    threshold_severe: float = 300.0
    threshold_kill: float = 1800.0
    _last_event_at: float | None = None
    _retry_until: float | None = None

    def start_turn(self, now: float) -> None:
        """Begin a new user message: reset the quiet clock."""
        self._last_event_at = now
        self._retry_until = None

    def record_event(self, now: float) -> None:
        """Note any CC event arrived; clears an expired retry backoff."""
        self._last_event_at = now
        if self._retry_until is not None and now >= self._retry_until:
            self._retry_until = None

    def record_retry(self, now: float, retry_delay_ms: float) -> None:
        """CC entered an api_retry backoff; don't treat the wait as silence."""
        self._retry_until = now + retry_delay_ms / 1000.0

    def quiet_seconds(self, now: float) -> float:
        """Return seconds elapsed since the last event (0 if no turn started)."""
        if self._last_event_at is None:
            return 0.0
        return now - self._last_event_at

    def phase(self, now: float) -> str:
        """Return the current stall phase.

        One of ``"quiet"`` | ``"mild"`` | ``"severe"`` | ``"kill"``:
        - quiet: healthy (silent < threshold_mild), inside an api_retry backoff,
          or no turn started
        - mild: past threshold_mild — gentle heads-up
        - severe: past threshold_severe — strong heads-up
        - kill: past threshold_kill — hard cap, service treats as fatal
        """
        if self._last_event_at is None:
            return "quiet"
        if self._retry_until is not None and now < self._retry_until:
            return "quiet"  # inside an api_retry backoff — waiting, not dead
        quiet = now - self._last_event_at
        if quiet >= self.threshold_kill:
            return "kill"
        if quiet >= self.threshold_severe:
            return "severe"
        if quiet >= self.threshold_mild:
            return "mild"
        return "quiet"
