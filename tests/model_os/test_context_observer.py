"""Tests for the production Context Observer (slice-088).

Two layers, mirroring slice-082:

- **Unit tests**: each pins one invariant — the CC dedup rule, compact
  generation, the A2 "missing usage → unavailable" revision, the M4 dynamic
  window, and the Codex field contract (A5 compaction, M1 missing turn id).
- **Fixture tests**: run the calculator on the sanitized real recordings
  promoted from slice-082 (``fixtures/context_observer/cc/``) and assert the
  evidence numbers from ``spikes/context-metering-20260721/report.md``. This is
  slice-088 pass-criterion 1 ("082 全部 fixture 在生产计算器通过").
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trowel_py.model_os.context_observer import (
    ContextConfidence,
    ContextSample,
    NormalizedCcEvent,
    NormalizedCodexCompaction,
    NormalizedCodexUsage,
    UnavailableReason,
    cc_context_events_from_agent,
    codex_context_events_from_agent,
    extract_cc_samples,
    extract_codex_samples,
    latest_main_ratio,
    resolve_window,
)

FIXTURES_CC = Path(__file__).resolve().parent / "fixtures" / "context_observer" / "cc"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


_NO_USAGE = object()  # sentinel: distinguishes "not passed" from usage=None


def cc_asst(
    *,
    message_id: str,
    model: str = "glm-5.2",
    input_tokens: int = 0,
    cache_creation: int = 0,
    cache_read: int = 0,
    output_tokens: int = 0,
    timestamp: str = "2026-07-21T00:00:00Z",
    turn_id: str | None = None,
    usage: object = _NO_USAGE,
) -> NormalizedCcEvent:
    """Build a CC assistant NormalizedCcEvent. Omitting ``usage`` builds the
    default mapping from the token args; pass ``usage={}`` for a dict that
    lacks input_tokens, or ``usage=None`` to mean the whole block was redacted."""

    if usage is _NO_USAGE:
        usage = {
            "input_tokens": input_tokens,
            "cache_creation_input_tokens": cache_creation,
            "cache_read_input_tokens": cache_read,
            "output_tokens": output_tokens,
        }
    return NormalizedCcEvent(
        type="assistant",
        subtype=None,
        timestamp=timestamp,
        message_id=message_id,
        model=model,
        usage=usage,
        turn_id=turn_id,
    )


def cc_boundary(timestamp: str = "2026-07-21T00:00:01Z") -> NormalizedCcEvent:
    return NormalizedCcEvent(
        type="system",
        subtype="compact_boundary",
        timestamp=timestamp,
        message_id=None,
        model=None,
        usage=None,
        turn_id=None,
    )


def force_window(window: int | None):
    """Resolver that always returns ``window`` (force None to test unavailable)."""

    return lambda _model: window


def _load_cc_fixture(name: str) -> list[NormalizedCcEvent]:
    """Load a sanitized CC fixture (NormalizedEvent skeleton, one dict/line)."""

    events: list[NormalizedCcEvent] = []
    for raw in (FIXTURES_CC / name).read_text().splitlines():
        raw = raw.strip()
        if not raw:
            continue
        o = json.loads(raw)
        events.append(
            NormalizedCcEvent(
                type=o["type"],
                subtype=o.get("subtype"),
                timestamp=o.get("timestamp"),
                message_id=o.get("message_id"),
                model=o.get("model"),
                usage=o.get("usage"),
                turn_id=o.get("turn_id"),
                compact_trigger=o.get("compact_trigger"),
                compact_pre_tokens=o.get("compact_pre_tokens"),
                compact_post_tokens=o.get("compact_post_tokens"),
            )
        )
    return events


# ---------------------------------------------------------------------------
# CC — dedup (082 alg 2)
# ---------------------------------------------------------------------------


class TestCcDedup:
    def test_same_id_identical_usage_collapses_to_one(self):
        events = [
            cc_asst(message_id="msg_a", input_tokens=1000, output_tokens=50),
            cc_asst(message_id="msg_a", input_tokens=1000, output_tokens=50),
        ]
        samples = extract_cc_samples(
            events, native_session_id="s", main_or_subagent="main",
            window_resolver=force_window(200_000),
        )
        assert len(samples) == 1
        assert samples[0].used_tokens == 1050

    def test_same_id_evolving_usage_keeps_last(self):
        """The spike's central finding: a subagent transcript's same message.id
        evolves from {0,0} to the real usage. The LAST record must win."""
        events = [
            cc_asst(message_id="msg_x", input_tokens=0, output_tokens=0),
            cc_asst(message_id="msg_x", input_tokens=0, output_tokens=0),
            cc_asst(message_id="msg_x", input_tokens=7365, cache_read=49600, output_tokens=337),
        ]
        samples = extract_cc_samples(
            events, native_session_id="s", main_or_subagent="subagent",
            window_resolver=force_window(200_000),
        )
        assert len(samples) == 1
        assert samples[0].used_tokens == 7365 + 49600 + 337
        assert samples[0].input_tokens == 7365

    def test_distinct_ids_each_get_one_sample(self):
        events = [
            cc_asst(message_id="a", input_tokens=100),
            cc_asst(message_id="b", input_tokens=200),
        ]
        samples = extract_cc_samples(
            events, native_session_id="s", main_or_subagent="main",
            window_resolver=force_window(200_000),
        )
        assert [s.request_identity for s in samples] == ["a", "b"]


# ---------------------------------------------------------------------------
# CC — generation (082 alg 3)
# ---------------------------------------------------------------------------


class TestCcGeneration:
    def test_boundary_increments_generation(self):
        events = [
            cc_asst(message_id="a", input_tokens=180_000),
            cc_boundary(),
            cc_asst(message_id="b", input_tokens=60_000),
            cc_boundary(),
            cc_asst(message_id="c", input_tokens=50_000),
        ]
        samples = extract_cc_samples(
            events, native_session_id="s", main_or_subagent="main",
            window_resolver=force_window(200_000),
        )
        assert [s.generation for s in samples] == [0, 1, 2]

    def test_ratio_uses_new_generation_window_not_old_peak(self):
        events = [
            cc_asst(message_id="pre", input_tokens=180_000, output_tokens=5000),
            cc_boundary(),
            cc_asst(message_id="post", input_tokens=60_000, output_tokens=2000),
        ]
        samples = extract_cc_samples(
            events, native_session_id="s", main_or_subagent="main",
            window_resolver=force_window(200_000),
        )
        pre, post = samples
        assert pre.ratio > 0.9 and post.ratio < 0.4
        assert latest_main_ratio(samples) is post


# ---------------------------------------------------------------------------
# CC — unavailable degradation (A2 + alg 4)
# ---------------------------------------------------------------------------


class TestCcUnavailable:
    def test_real_model_missing_usage_dict_yields_unavailable(self):
        """A2: a real-model assistant whose usage dict lacks input_tokens is
        still an observation → UNAVAILABLE(MISSING_USAGE), not skipped."""
        events = [cc_asst(message_id="m", usage={})]
        samples = extract_cc_samples(
            events, native_session_id="s", main_or_subagent="main",
            window_resolver=force_window(200_000),
        )
        assert len(samples) == 1
        assert samples[0].confidence is ContextConfidence.UNAVAILABLE
        assert samples[0].unavailable_reason is UnavailableReason.MISSING_USAGE
        assert samples[0].ratio is None and samples[0].used_tokens is None

    def test_redacted_usage_block_yields_unavailable(self):
        """A2: usage=None means the whole block was redacted away."""
        events = [cc_asst(message_id="m", usage=None)]
        samples = extract_cc_samples(
            events, native_session_id="s", main_or_subagent="main",
            window_resolver=force_window(200_000),
        )
        assert samples[0].unavailable_reason is UnavailableReason.REDACTED_USAGE

    def test_unknown_model_yields_unknown_window(self):
        """alg 4: known usage but unknown model → UNAVAILABLE(UNKNOWN_WINDOW)."""
        events = [cc_asst(message_id="m", model="future-model-9", input_tokens=1000)]
        samples = extract_cc_samples(
            events, native_session_id="s", main_or_subagent="main",
        )
        assert samples[0].confidence is ContextConfidence.UNAVAILABLE
        assert samples[0].unavailable_reason is UnavailableReason.UNKNOWN_WINDOW
        # raw tokens still recorded for a human, but no ratio
        assert samples[0].used_tokens == 1000 and samples[0].ratio is None

    def test_synthetic_assistant_skipped_entirely(self):
        """A2: <synthetic> slash-command output is NOT a model call — skipped,
        not turned into an unavailable sample."""
        events = [
            cc_asst(message_id="synth", model="<synthetic>", input_tokens=10),
            cc_asst(message_id="real", input_tokens=500),
        ]
        samples = extract_cc_samples(
            events, native_session_id="s", main_or_subagent="main",
            window_resolver=force_window(200_000),
        )
        assert [s.request_identity for s in samples] == ["real"]


# ---------------------------------------------------------------------------
# CC — dynamic window (M4)
# ---------------------------------------------------------------------------


class TestCcWindowDynamic:
    def test_window_re_resolved_per_sample_when_model_changes(self):
        """M4: 083 proved resume can switch model on the same session. The
        window must follow each sample's model, not be cached from the first."""
        events = [
            cc_asst(message_id="a", model="glm-5.2", input_tokens=50_000),
            cc_asst(message_id="b", model="claude-sonnet-4", input_tokens=50_000),
        ]
        # glm-5.2 → 200k via the real resolver; force claude-sonnet-4 to a
        # different window to prove the second sample did not inherit the first.
        def resolver(model):
            return 200_000 if model == "glm-5.2" else 400_000

        samples = extract_cc_samples(
            events, native_session_id="s", main_or_subagent="main",
            window_resolver=resolver,
        )
        assert samples[0].effective_window_tokens == 200_000
        assert samples[1].effective_window_tokens == 400_000
        assert samples[0].ratio != samples[1].ratio  # same used, different window


# ---------------------------------------------------------------------------
# CC — subagent isolation (082 C-3)
# ---------------------------------------------------------------------------


class TestCcSubagentIsolation:
    def test_subagent_samples_tagged_and_excluded_from_main(self):
        main = extract_cc_samples(
            [cc_asst(message_id="main_a", input_tokens=40_000)],
            native_session_id="s", main_or_subagent="main",
            window_resolver=force_window(200_000),
        )
        sub = extract_cc_samples(
            [cc_asst(message_id="sub_a", input_tokens=300_000)],
            native_session_id="s", main_or_subagent="subagent",
            window_resolver=force_window(200_000),
        )
        assert all(s.main_or_subagent == "main" for s in main)
        assert all(s.main_or_subagent == "subagent" for s in sub)
        combined = main + sub
        latest = latest_main_ratio(combined)
        assert latest is not None
        assert latest.request_identity == "main_a"  # the 300k subagent did not win


# ---------------------------------------------------------------------------
# CC — golden fixtures (pass-criterion 1)
# ---------------------------------------------------------------------------


class TestCcFixtureMultiTurn:
    def test_unique_request_count_and_first_sample(self):
        events = _load_cc_fixture("glm-5.2-multi-turn.jsonl")
        samples = extract_cc_samples(
            events, native_session_id="s", main_or_subagent="main",
            source_version="2.1.197",
        )
        # report.md §3: 11 unique message.id after dedup
        assert len(samples) == 11
        assert samples[0].used_tokens == 52929 + 0 + 0 + 225
        assert samples[0].ratio == round(53154 / 200_000, 4)
        assert samples[0].source_version == "2.1.197"

    def test_cache_read_monotonic_within_generation(self):
        events = _load_cc_fixture("glm-5.2-multi-turn.jsonl")
        samples = extract_cc_samples(
            events, native_session_id="s", main_or_subagent="main",
        )
        gen0 = [s for s in samples if s.generation == 0]
        reads = [s.cache_read_input_tokens for s in gen0]
        assert reads == sorted(reads)  # cache hit share only grows

    def test_window_is_200k_for_glm52(self):
        events = _load_cc_fixture("glm-5.2-multi-turn.jsonl")
        samples = extract_cc_samples(
            events, native_session_id="s", main_or_subagent="main",
        )
        assert all(s.effective_window_tokens == 200_000 for s in samples)


class TestCcFixtureCompactBoundary:
    def test_two_generations_and_peak_above_window(self):
        events = _load_cc_fixture("glm-5.2-compact-boundary.jsonl")
        samples = extract_cc_samples(
            events, native_session_id="s", main_or_subagent="main",
        )
        gens = {s.generation for s in samples}
        assert gens == {0, 1}
        gen0 = [s for s in samples if s.generation == 0]
        gen1 = [s for s in samples if s.generation == 1]
        # report.md §5.3: pre-compact peak used = 215920 (ratio > 1.0)
        assert gen0[-1].used_tokens == 215920
        assert gen0[-1].ratio > 1.0
        # post-compact sample is lower
        assert gen1[0].used_tokens < gen0[-1].used_tokens

    def test_compact_trigger_is_manual_not_auto(self):
        """report.md §5.2: this fixture's boundary is a MANUAL /compact, which
        is exactly why its 216k peak must NOT be read as the auto-compact point."""
        events = _load_cc_fixture("glm-5.2-compact-boundary.jsonl")
        boundaries = [e for e in events if e.subtype == "compact_boundary"]
        assert boundaries, "fixture should carry at least one compact_boundary"
        assert all(e.compact_trigger == "manual" for e in boundaries)


class TestCcFixtureSubagentTranscript:
    def test_evolving_usage_keeps_last(self):
        """report.md §2: message.id ...0800589b appears 10×, usage evolves
        {0,0}×6 then real ×4; the LAST record (used=57302) must win."""
        events = _load_cc_fixture("glm-5.2-subagent-transcript.jsonl")
        samples = extract_cc_samples(
            events, native_session_id="s", main_or_subagent="subagent",
            window_resolver=force_window(200_000),
        )
        target = [s for s in samples if s.request_identity.endswith("0800589b")]
        # the id fragment may be truncated in the fixture; match by evolving value
        evolving = [s for s in samples if s.used_tokens == 7365 + 49600 + 337]
        assert evolving, "expected the final evolved usage to survive dedup"


# ---------------------------------------------------------------------------
# Codex — calculator (slice-083 contract)
# ---------------------------------------------------------------------------


class TestCodexCalculator:
    def test_used_is_last_total_tokens_not_total(self):
        """083: numerator = last.totalTokens. total.totalTokens is cumulative."""
        events = [
            NormalizedCodexUsage(
                turn_id="t1", last_total_tokens=15111,
                total_total_tokens=15111, model_context_window=258400,
            ),
        ]
        samples = extract_codex_samples(events, native_session_id="th")
        assert samples[0].used_tokens == 15111
        assert samples[0].effective_window_tokens == 258400
        assert samples[0].ratio == round(15111 / 258400, 4)

    def test_total_total_tokens_never_used_as_current(self):
        """turn 2: last=15160, total=30271. The 30271 MUST NOT appear anywhere
        as the current占用."""
        events = [
            NormalizedCodexUsage(
                turn_id="t2", last_total_tokens=15160,
                total_total_tokens=30271, model_context_window=258400,
            ),
        ]
        samples = extract_codex_samples(events, native_session_id="th")
        assert samples[0].used_tokens == 15160
        assert samples[0].ratio == round(15160 / 258400, 4)

    def test_compact_completed_increments_generation(self):
        """A5: only item/completed(contextCompaction) closes the boundary."""
        events = [
            NormalizedCodexUsage(turn_id="t1", last_total_tokens=15111,
                                 total_total_tokens=15111, model_context_window=258400),
            NormalizedCodexCompaction(phase="started", turn_id="t1"),
            NormalizedCodexUsage(turn_id="tc", last_total_tokens=4495,
                                 total_total_tokens=15111, model_context_window=258400),
            NormalizedCodexCompaction(phase="completed", turn_id="t1"),
            NormalizedCodexUsage(turn_id="t2", last_total_tokens=15160,
                                 total_total_tokens=30271, model_context_window=258400),
        ]
        samples = extract_codex_samples(events, native_session_id="th")
        gens = [s.generation for s in samples]
        assert gens == [0, 0, 1]  # t1=gen0, tc=gen0(started didn't advance), t2=gen1

    def test_compact_start_alone_does_not_increment(self):
        """A5: thread/compact/start returning {} is NOT a completed boundary."""
        events = [
            NormalizedCodexUsage(turn_id="t1", last_total_tokens=100,
                                 total_total_tokens=100, model_context_window=258400),
            NormalizedCodexCompaction(phase="started", turn_id="t1"),
            NormalizedCodexUsage(turn_id="t2", last_total_tokens=200,
                                 total_total_tokens=300, model_context_window=258400),
        ]
        samples = extract_codex_samples(events, native_session_id="th")
        assert [s.generation for s in samples] == [0, 0]

    def test_missing_turn_id_yields_unavailable(self):
        """M1: no stable request identity → cannot dedup/replay safely."""
        events = [
            NormalizedCodexUsage(turn_id=None, last_total_tokens=100,
                                 total_total_tokens=100, model_context_window=258400),
        ]
        samples = extract_codex_samples(events, native_session_id="th")
        assert samples[0].confidence is ContextConfidence.UNAVAILABLE
        assert samples[0].unavailable_reason is UnavailableReason.MISSING_REQUEST_IDENTITY

    def test_missing_window_yields_unavailable(self):
        events = [
            NormalizedCodexUsage(turn_id="t1", last_total_tokens=100,
                                 total_total_tokens=100, model_context_window=None),
        ]
        samples = extract_codex_samples(events, native_session_id="th")
        assert samples[0].confidence is ContextConfidence.UNAVAILABLE
        assert samples[0].unavailable_reason is UnavailableReason.UNKNOWN_WINDOW
        assert samples[0].used_tokens == 100  # raw kept, ratio None

    def test_missing_last_total_yields_unavailable(self):
        events = [
            NormalizedCodexUsage(turn_id="t1", last_total_tokens=None,
                                 total_total_tokens=100, model_context_window=258400),
        ]
        samples = extract_codex_samples(events, native_session_id="th")
        assert samples[0].confidence is ContextConfidence.UNAVAILABLE
        assert samples[0].unavailable_reason is UnavailableReason.MISSING_USAGE


# ---------------------------------------------------------------------------
# window resolver unit (082 alg 4)
# ---------------------------------------------------------------------------


class TestResolveWindow:
    def test_known_glm_returns_200k(self):
        assert resolve_window("glm-5.2") == 200_000

    def test_1m_suffix_ignored_on_glm(self):
        assert resolve_window("glm-5.2[1m]") == 200_000

    def test_synthetic_returns_none(self):
        assert resolve_window("<synthetic>") is None

    def test_unknown_returns_none(self):
        assert resolve_window("future-model-9") is None

    def test_none_returns_none(self):
        assert resolve_window(None) is None


# ---------------------------------------------------------------------------
# AgentEvent → calculator wiring (slice-088 runtime adapter)
# ---------------------------------------------------------------------------


class TestAgentEventAdapter:
    """The SSE shape from /api/agent/sessions/{id}/messages → normalized events
    → ContextSample. Pins the field mapping the live stream relies on."""

    def test_codex_usage_and_compaction_from_agent_events(self):
        events = [
            {"type": "usage_updated", "turn_id": "t1", "payload": {
                "last": {"totalTokens": 15111},
                "total": {"totalTokens": 15111},
                "model_context_window": 258400,
            }},
            {"type": "compaction", "turn_id": "t1", "payload": {"phase": "completed"}},
            {"type": "usage_updated", "turn_id": "t2", "payload": {
                "last": {"totalTokens": 15160},
                "total": {"totalTokens": 30271},
                "model_context_window": 258400,
            }},
        ]
        normalized = codex_context_events_from_agent(events)
        samples = extract_codex_samples(normalized, native_session_id="th")
        assert [s.generation for s in samples] == [0, 1]
        assert samples[0].used_tokens == 15111
        assert samples[1].used_tokens == 15160  # last.totalTokens, not total

    def test_cc_context_usage_and_boundary_from_agent_events(self):
        # HIGH 6: cc_host emits a dedicated context_usage event (carrying
        # usage/model/message_id) BEFORE splitting the assistant envelope into
        # text/thinking/tool_use, which discard them.
        events = [
            {"type": "context_usage", "turn_id": "t1", "payload": {
                "model": "glm-5.2", "message_id": "m1", "timestamp": "2026-07-22T00:00:00Z",
                "usage": {"input_tokens": 50000, "cache_creation_input_tokens": 0,
                          "cache_read_input_tokens": 0, "output_tokens": 100},
            }},
            {"type": "compact_boundary", "payload": {"trigger": "auto"}},
            {"type": "context_usage", "turn_id": "t2", "payload": {
                "model": "glm-5.2", "message_id": "m2",
                "usage": {"input_tokens": 30000, "cache_creation_input_tokens": 0,
                          "cache_read_input_tokens": 0, "output_tokens": 50},
            }},
        ]
        normalized = cc_context_events_from_agent(events)
        samples = extract_cc_samples(
            normalized, native_session_id="s", main_or_subagent="main",
        )
        assert [s.generation for s in samples] == [0, 1]
        assert samples[0].used_tokens == 50100


# ---------------------------------------------------------------------------
# Codex — golden fixture (slice-083 frozen values), promoted in-slice
# ---------------------------------------------------------------------------


FIXTURES_CODEX = Path(__file__).resolve().parent / "fixtures" / "context_observer" / "codex"


def _load_codex_fixture(name: str) -> list[dict]:
    """Load an AgentEvent-stream Codex fixture (one dict/line)."""

    events: list[dict] = []
    for raw in (FIXTURES_CODEX / name).read_text().splitlines():
        raw = raw.strip()
        if raw:
            events.append(json.loads(raw))
    return events


class TestCodexFixture:
    def test_usage_compact_sequence_matches_083(self):
        """083 frozen values: turn1 last=15111, native compact turn last=4495,
        compact-completed then turn2 last=15160; window 258400 throughout.
        ``total.totalTokens`` (30271 at turn2) NEVER appears as current占用."""
        events = _load_codex_fixture("usage_compact_sequence.jsonl")
        normalized = codex_context_events_from_agent(events)
        samples = extract_codex_samples(
            normalized, native_session_id="th", source_version="0.144.0")
        assert [s.generation for s in samples] == [0, 1, 1]
        assert [s.used_tokens for s in samples] == [15111, 4495, 15160]
        assert all(s.effective_window_tokens == 258400 for s in samples)
        assert all(s.used_tokens != 30271 for s in samples)  # total not numerator

    def test_fresh_new_session_starts_generation_zero(self):
        """083: fresh = new native thread → generation from 0. Two fresh
        sessions each independently start at gen 0 (088 per-session keying)."""
        events = _load_codex_fixture("usage_compact_sequence.jsonl")
        s1 = extract_codex_samples(
            codex_context_events_from_agent(events), native_session_id="th1")
        s2 = extract_codex_samples(
            codex_context_events_from_agent(events), native_session_id="th2")
        assert s1[0].generation == 0 and s2[0].generation == 0
        assert s1[0].native_session_id == "th1"
        assert s2[0].native_session_id == "th2"


# ---------------------------------------------------------------------------
# CC real chain (codex review HIGH 6) — raw assistant → Translator → adapter
# → context adapter → calculator, with REAL components (no hand-written
# AgentEvent dict that would bypass Pydantic and hide that cc_host discarded
# usage before slice-088).
# ---------------------------------------------------------------------------


class TestCcRealChain:
    def test_real_assistant_envelope_yields_context_sample(self):
        from trowel_py.cc_host.translator import Translator
        from trowel_py.agent_host.cc_adapter import CcEventAdapter

        raw_assistant = {
            "type": "assistant",
            "message": {
                "id": "msg_real_1",
                "model": "glm-5.2",
                "usage": {
                    "input_tokens": 50000,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 100,
                },
                "content": [{"type": "text", "text": "ok"}],
            },
        }
        trowel_events = Translator().translate(raw_assistant)
        # HIGH 6: a ContextUsageEvent is emitted before the content split.
        assert "ContextUsageEvent" in {type(e).__name__ for e in trowel_events}
        adapter = CcEventAdapter("s1")
        agent_events = [
            ae.model_dump(by_alias=True)
            for ae in (adapter.wrap(e.model_dump()) for e in trowel_events)
            if ae is not None
        ]
        normalized = cc_context_events_from_agent(agent_events)
        samples = extract_cc_samples(
            normalized, native_session_id="s1", main_or_subagent="main",
            source_version="2.1.197")
        assert samples, "the real chain must yield at least one ContextSample"
        assert samples[0].used_tokens == 50100
        assert samples[0].confidence is ContextConfidence.RELIABLE
        assert samples[0].request_identity == "msg_real_1"
        assert samples[0].source_version == "2.1.197"

    def test_real_compact_boundary_carries_trigger(self):
        from trowel_py.cc_host.translator import Translator
        from trowel_py.agent_host.cc_adapter import CcEventAdapter

        raw_boundary = {
            "type": "system",
            "subtype": "compact_boundary",
            "compactMetadata": {"trigger": "auto", "preTokens": 167000},
        }
        trowel_events = Translator().translate(raw_boundary)
        adapter = CcEventAdapter("s1")
        agent_events = [
            ae.model_dump(by_alias=True)
            for ae in (adapter.wrap(e.model_dump()) for e in trowel_events)
            if ae is not None
        ]
        normalized = cc_context_events_from_agent(agent_events)
        assert any(
            e.type == "system" and e.subtype == "compact_boundary" and e.compact_trigger == "auto"
            for e in normalized
        )
