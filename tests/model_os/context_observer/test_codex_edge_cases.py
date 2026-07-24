from trowel_py.model_os.context_observer import (
    ContextConfidence,
    NormalizedCodexCompaction,
    NormalizedCodexUsage,
    UnavailableReason,
    extract_codex_samples,
)


def usage(
    *,
    turn_id: str | None = "turn-example",
    used_tokens: int | None = 100,
    window: int | None = 1_000,
) -> NormalizedCodexUsage:
    return NormalizedCodexUsage(
        turn_id=turn_id,
        last_total_tokens=used_tokens,
        total_total_tokens=999_999,
        model_context_window=window,
    )


def test_non_positive_window_preserves_usage_but_has_no_ratio() -> None:
    for window in (0, -1):
        sample = extract_codex_samples(
            [usage(window=window)],
            native_session_id="session-example",
        )[0]
        assert sample.used_tokens == 100
        assert sample.effective_window_tokens is None
        assert sample.ratio is None
        assert sample.confidence is ContextConfidence.UNAVAILABLE
        assert sample.unavailable_reason is UnavailableReason.UNKNOWN_WINDOW


def test_missing_turn_identity_takes_precedence_over_missing_usage() -> None:
    for turn_id in (None, ""):
        sample = extract_codex_samples(
            [usage(turn_id=turn_id, used_tokens=None, window=None)],
            native_session_id="session-example",
        )[0]
        assert sample.unavailable_reason is UnavailableReason.MISSING_REQUEST_IDENTITY


def test_missing_usage_takes_precedence_over_invalid_window() -> None:
    sample = extract_codex_samples(
        [usage(used_tokens=None, window=0)],
        native_session_id="session-example",
    )[0]
    assert sample.unavailable_reason is UnavailableReason.MISSING_USAGE


def test_cumulative_total_does_not_change_the_sample() -> None:
    first = usage()
    second = NormalizedCodexUsage(
        turn_id=first.turn_id,
        last_total_tokens=first.last_total_tokens,
        total_total_tokens=-999_999,
        model_context_window=first.model_context_window,
    )

    assert extract_codex_samples(
        [first],
        native_session_id="session-example",
    ) == extract_codex_samples(
        [second],
        native_session_id="session-example",
    )


def test_unknown_compaction_phase_is_ignored_without_advancing_generation() -> None:
    samples = extract_codex_samples(
        [
            usage(turn_id="turn-before"),
            NormalizedCodexCompaction(
                phase="future-phase",
                turn_id="turn-before",
            ),
            usage(turn_id="turn-after"),
        ],
        native_session_id="session-example",
    )
    assert [sample.generation for sample in samples] == [0, 0]


def test_each_completed_compaction_advances_one_generation() -> None:
    samples = extract_codex_samples(
        [
            NormalizedCodexCompaction(phase="completed", turn_id="turn-1"),
            NormalizedCodexCompaction(phase="completed", turn_id="turn-2"),
            usage(turn_id="turn-after"),
        ],
        native_session_id="session-example",
    )
    assert len(samples) == 1
    assert samples[0].generation == 2
