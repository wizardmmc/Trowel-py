from __future__ import annotations

from trowel_py.model_os.context_observer import (
    ContextConfidence,
    NormalizedCodexCompaction,
    NormalizedCodexUsage,
    UnavailableReason,
    extract_codex_samples,
    resolve_window,
)


class TestCodexCalculator:
    def test_used_is_last_total_tokens_not_total(self):
        events = [
            NormalizedCodexUsage(
                turn_id="t1",
                last_total_tokens=15111,
                total_total_tokens=15111,
                model_context_window=258400,
            ),
        ]
        samples = extract_codex_samples(events, native_session_id="th")
        assert samples[0].used_tokens == 15111
        assert samples[0].effective_window_tokens == 258400
        assert samples[0].ratio == round(15111 / 258400, 4)

    def test_total_total_tokens_never_used_as_current(self):
        events = [
            NormalizedCodexUsage(
                turn_id="t2",
                last_total_tokens=15160,
                total_total_tokens=30271,
                model_context_window=258400,
            ),
        ]
        samples = extract_codex_samples(events, native_session_id="th")
        assert samples[0].used_tokens == 15160
        assert samples[0].ratio == round(15160 / 258400, 4)

    def test_compact_completed_increments_generation(self):
        events = [
            NormalizedCodexUsage(
                turn_id="t1",
                last_total_tokens=15111,
                total_total_tokens=15111,
                model_context_window=258400,
            ),
            NormalizedCodexCompaction(phase="started", turn_id="t1"),
            NormalizedCodexUsage(
                turn_id="tc",
                last_total_tokens=4495,
                total_total_tokens=15111,
                model_context_window=258400,
            ),
            NormalizedCodexCompaction(phase="completed", turn_id="t1"),
            NormalizedCodexUsage(
                turn_id="t2",
                last_total_tokens=15160,
                total_total_tokens=30271,
                model_context_window=258400,
            ),
        ]
        samples = extract_codex_samples(events, native_session_id="th")
        gens = [s.generation for s in samples]
        assert gens == [0, 0, 1]

    def test_compact_start_alone_does_not_increment(self):
        events = [
            NormalizedCodexUsage(
                turn_id="t1",
                last_total_tokens=100,
                total_total_tokens=100,
                model_context_window=258400,
            ),
            NormalizedCodexCompaction(phase="started", turn_id="t1"),
            NormalizedCodexUsage(
                turn_id="t2",
                last_total_tokens=200,
                total_total_tokens=300,
                model_context_window=258400,
            ),
        ]
        samples = extract_codex_samples(events, native_session_id="th")
        assert [s.generation for s in samples] == [0, 0]

    def test_missing_turn_id_yields_unavailable(self):
        events = [
            NormalizedCodexUsage(
                turn_id=None,
                last_total_tokens=100,
                total_total_tokens=100,
                model_context_window=258400,
            ),
        ]
        samples = extract_codex_samples(events, native_session_id="th")
        assert samples[0].confidence is ContextConfidence.UNAVAILABLE
        assert (
            samples[0].unavailable_reason is UnavailableReason.MISSING_REQUEST_IDENTITY
        )

    def test_missing_window_yields_unavailable(self):
        events = [
            NormalizedCodexUsage(
                turn_id="t1",
                last_total_tokens=100,
                total_total_tokens=100,
                model_context_window=None,
            ),
        ]
        samples = extract_codex_samples(events, native_session_id="th")
        assert samples[0].confidence is ContextConfidence.UNAVAILABLE
        assert samples[0].unavailable_reason is UnavailableReason.UNKNOWN_WINDOW
        assert samples[0].used_tokens == 100

    def test_missing_last_total_yields_unavailable(self):
        events = [
            NormalizedCodexUsage(
                turn_id="t1",
                last_total_tokens=None,
                total_total_tokens=100,
                model_context_window=258400,
            ),
        ]
        samples = extract_codex_samples(events, native_session_id="th")
        assert samples[0].confidence is ContextConfidence.UNAVAILABLE
        assert samples[0].unavailable_reason is UnavailableReason.MISSING_USAGE


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
