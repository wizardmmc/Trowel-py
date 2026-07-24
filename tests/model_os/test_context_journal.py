"""Context 观测的 journal 持久化、幂等身份和 reducer 投影测试。"""

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
    """构造默认可信的 ContextSample；降级场景显式传入不可用值。"""

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
        sample = _ctx_sample(used=57_302, ratio=0.2865, request_id="msg_x")
        seq1 = store.record_context_sample(
            sample, episode_id="ep1", occurred_at="2026-07-22T00:00:00Z"
        )
        seq2 = store.record_context_sample(
            sample, episode_id="ep1", occurred_at="2026-07-22T00:00:01Z"
        )
        assert seq1 == seq2  # occurred_at 不参与 event_id
        snap = store.replay()
        assert len(snap.context_observations) == 1

    def test_unavailable_not_masked_by_older_trusted(self, store):
        store.record_context_sample(
            _ctx_sample(used=50_000, ratio=0.25),
            episode_id="ep1",
            occurred_at="2026-07-22T00:00:00Z",
        )
        store.record_context_sample(
            _ctx_sample(
                used=None,
                ratio=None,
                request_id="r2",
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
        store.record_context_boundary(
            "s1",
            episode_id="ep1",
            generation=1,
            occurred_at="2026-07-22T00:00:00Z",
            trigger="auto",
        )
        snap = store.replay()
        assert len(snap.context_observations) == 0
        assert len(store.list_events()) == 1  # 事件保留供审计

    def test_latest_across_sessions_picks_observed_at_max(self, store):
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
        store.record_context_sample(
            _ctx_sample(used=10_000, ratio=0.05, native="s1"),
            episode_id="ep1",
            occurred_at="2026-07-22T00:00:00Z",
        )
        store.record_context_sample(
            _ctx_sample(used=80_000, ratio=0.4, native="s2"),
            episode_id="ep1",
            occurred_at="2026-07-22T00:00:01Z",
        )
        snap = store.replay()
        assert snap.context_observation("ep1", "s2").latest_sample.used_tokens == 80_000
        assert snap.context_observation("ep1", "s1").latest_sample.used_tokens == 10_000

    def test_boundary_lands_before_sample_in_replay(self, store):
        store.record_context_boundary(
            "s1",
            episode_id="ep1",
            generation=1,
            occurred_at="2026-07-22T00:00:00Z",
            trigger="auto",
        )
        store.record_context_sample(
            _ctx_sample(used=60_000, ratio=0.3, gen=1),
            episode_id="ep1",
            occurred_at="2026-07-22T00:00:01Z",
        )
        events = store.list_events()
        assert events[0][1].kind == "context.generation_boundary"
        assert events[1][1].kind == "context.sample_observed"
        snap = store.replay()
        obs = snap.context_observation("ep1", "s1")
        assert obs.generation == 1  # generation 来自样本而非边界事件
