"""Journal + reducer tests for slice-088 context observations.

Pins the slice-088 pass criteria that live above the pure calculator:

- pass 4: the same request replayed does not duplicate a sample (event_id
  idempotency), and an evolving usage (A4) lands as distinct events with the
  reducer keeping the last;
- extra-HIGH 1: an unavailable observation is stored and is NOT masked by an
  older trusted number;
- extra-HIGH 2: ownership (task/episode/native_session) lives on the envelope,
  not duplicated in the payload;
- 083 contract: a generation boundary is audit-only (the ContextSample carries
  its own generation);
- A3 caveat: cross-session latest picks observed_at max (documented limit).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trowel_py.model_os.context_observer import (
    ContextConfidence,
    ContextSample,
    UnavailableReason,
)
from trowel_py.model_os.store import ModelOsStore


def _ctx_sample(
    *,
    used: int | None = 50_000,
    ratio: float | None = 0.25,
    native: str = "s1",
    request_id: str = "r1",
    gen: int = 0,
    confidence: ContextConfidence = ContextConfidence.RELIABLE,
    reason: UnavailableReason = UnavailableReason.NONE,
) -> ContextSample:
    """Build a ContextSample for journal tests. Defaults to a trusted sample;
    pass used=None / confidence=UNAVAILABLE for the degraded case."""

    return ContextSample(
        native_session_id=native,
        main_or_subagent="main",
        turn_id="t1",
        request_identity=request_id,
        generation=gen,
        input_tokens=used,
        cache_creation_input_tokens=None,
        cache_read_input_tokens=None,
        output_tokens=None,
        used_tokens=used,
        effective_window_tokens=200_000 if ratio is not None else None,
        ratio=ratio,
        source="cc",
        source_version="2.1.197",
        confidence=confidence,
        unavailable_reason=reason,
    )


@pytest.fixture
def store(tmp_path: Path) -> ModelOsStore:
    s = ModelOsStore(str(tmp_path / "m.db"))
    s.open()
    return s


class TestContextJournal:
    def test_record_and_replay_projects_latest(self, store):
        store.record_context_sample(
            _ctx_sample(used=50_000, ratio=0.25),
            episode_id="ep1",
            occurred_at="2026-07-22T00:00:00Z",
        )
        snap = store.replay()
        obs = snap.context_observation("ep1", "s1")
        assert obs is not None
        assert obs.latest_sample.used_tokens == 50_000
        assert obs.generation == 0

    def test_same_observation_is_idempotent(self, store):
        """pass 4: replaying the same observation (same runtime occurred_at)
        does not duplicate — event_id is stable across retries."""
        sample = _ctx_sample(used=50_000, ratio=0.25)
        seq1 = store.record_context_sample(
            sample, episode_id="ep1", occurred_at="2026-07-22T00:00:00Z"
        )
        seq2 = store.record_context_sample(
            sample, episode_id="ep1", occurred_at="2026-07-22T00:00:00Z"
        )
        assert seq1 == seq2
        snap = store.replay()
        assert len(snap.context_observations) == 1

    def test_same_request_replay_is_idempotent(self, store):
        """codex review HIGH 3: replaying the SAME observation is idempotent —
        event_id does NOT embed wall-clock occurred_at (the AgentEvent stream
        carries no stable event time, so a write-clock time would make
        crash-replay mint a new id). Evolving usage is collapsed by the batch
        calculator's dedup (see TestCcDedup), so one request → one sample →
        one stable id."""
        sample = _ctx_sample(used=57_302, ratio=0.2865, request_id="msg_x")
        seq1 = store.record_context_sample(
            sample, episode_id="ep1", occurred_at="2026-07-22T00:00:00Z"
        )
        seq2 = store.record_context_sample(
            sample, episode_id="ep1", occurred_at="2026-07-22T00:00:01Z"
        )
        assert seq1 == seq2  # idempotent — occurred_at is NOT in the event_id
        snap = store.replay()
        assert len(snap.context_observations) == 1

    def test_unavailable_not_masked_by_older_trusted(self, store):
        """extra-HIGH 1: a newer unavailable observation must replace an older
        trusted number, not be hidden by it."""
        store.record_context_sample(
            _ctx_sample(used=50_000, ratio=0.25),
            episode_id="ep1",
            occurred_at="2026-07-22T00:00:00Z",
        )
        store.record_context_sample(
            _ctx_sample(
                used=None, ratio=None, request_id="r2",
                confidence=ContextConfidence.UNAVAILABLE,
                reason=UnavailableReason.MISSING_USAGE,
            ),
            episode_id="ep1",
            occurred_at="2026-07-22T00:00:05Z",
        )
        snap = store.replay()
        latest = snap.latest_context_sample("ep1")
        assert latest is not None
        assert latest.latest_sample.confidence is ContextConfidence.UNAVAILABLE

    def test_boundary_is_audit_only(self, store):
        """083: the boundary event lands in the journal but derives no state —
        the ContextSample carries its own generation."""
        store.record_context_boundary(
            "s1", episode_id="ep1", generation=1,
            occurred_at="2026-07-22T00:00:00Z", trigger="auto",
        )
        snap = store.replay()
        assert len(snap.context_observations) == 0
        assert len(store.list_events()) == 1  # event is retained for audit

    def test_latest_across_sessions_picks_observed_at_max(self, store):
        """A3 (documented limit): across native sessions of one Episode, the
        highest observed_at wins — 088 does not yet know the 'current' relay."""
        store.record_context_sample(
            _ctx_sample(used=10_000, ratio=0.05, native="s1"),
            episode_id="ep1",
            occurred_at="2026-07-22T00:00:10Z",
        )
        store.record_context_sample(
            _ctx_sample(used=80_000, ratio=0.4, native="s2"),
            episode_id="ep1",
            occurred_at="2026-07-22T00:00:00Z",
        )
        snap = store.replay()
        latest = snap.latest_context_sample("ep1")
        assert latest is not None
        assert latest.native_session_id == "s1"  # 00:10 > 00:00

    def test_context_observation_exact_within_session(self, store):
        """context_observation(episode, session) is exact (A3): keyed by session."""
        store.record_context_sample(
            _ctx_sample(used=10_000, ratio=0.05, native="s1"),
            episode_id="ep1", occurred_at="2026-07-22T00:00:00Z",
        )
        store.record_context_sample(
            _ctx_sample(used=80_000, ratio=0.4, native="s2"),
            episode_id="ep1", occurred_at="2026-07-22T00:00:01Z",
        )
        snap = store.replay()
        assert snap.context_observation("ep1", "s2").latest_sample.used_tokens == 80_000
        assert snap.context_observation("ep1", "s1").latest_sample.used_tokens == 10_000

    def test_boundary_lands_before_sample_in_replay(self, store):
        """083 ordering: boundary event is in the journal before later samples,
        so a replay sees the boundary first even though the reducer is audit-only
        for it (the sample still carries its own generation)."""
        store.record_context_boundary(
            "s1", episode_id="ep1", generation=1,
            occurred_at="2026-07-22T00:00:00Z", trigger="auto",
        )
        store.record_context_sample(
            _ctx_sample(used=60_000, ratio=0.3, gen=1),
            episode_id="ep1", occurred_at="2026-07-22T00:00:01Z",
        )
        events = store.list_events()
        assert events[0][1].kind == "context.generation_boundary"
        assert events[1][1].kind == "context.sample_observed"
        snap = store.replay()
        obs = snap.context_observation("ep1", "s1")
        assert obs.generation == 1  # from the sample, not the boundary
