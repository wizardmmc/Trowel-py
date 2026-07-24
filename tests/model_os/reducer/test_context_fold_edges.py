from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from trowel_py.model_os import reducer
from trowel_py.model_os.context_observer import (
    ContextConfidence,
    ContextSample,
    UnavailableReason,
)
from trowel_py.model_os.types import EventEnvelope, EventKind, Provenance


def _sample(native: str, generation: int = 1) -> ContextSample:
    return ContextSample(
        native_session_id=native,
        main_or_subagent="main",
        turn_id=None,
        request_identity=f"request-{generation}",
        generation=generation,
        input_tokens=None,
        cache_creation_input_tokens=None,
        cache_read_input_tokens=None,
        output_tokens=None,
        used_tokens=None,
        effective_window_tokens=None,
        ratio=None,
        source="test",
        source_version=None,
        confidence=ContextConfidence.UNAVAILABLE,
        unavailable_reason=UnavailableReason.MISSING_USAGE,
    )


def _event(
    *,
    native: str | None = "session-1",
    occurred_at: str = "2026-07-24T00:00:00Z",
    payload: dict[str, Any] | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_id="event-context-fold-edge",
        kind=EventKind.CONTEXT_SAMPLE_OBSERVED,
        occurred_at=occurred_at,
        source="test",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v0",
        payload=payload or {},
        episode_id="episode-1",
        native_session_id=native,
    )


def _state(
    *,
    native: str = "session-1",
    observed_at: str,
    generation: int = 1,
) -> reducer.ContextObservationState:
    return reducer.ContextObservationState(
        episode_id="episode-1",
        native_session_id=native,
        generation=generation,
        latest_sample=_sample(native, generation),
        observed_at=observed_at,
    )


@pytest.mark.parametrize("native", [None, ""])
def test_missing_session_short_circuits_before_malformed_payload(native) -> None:
    snap = reducer.initial_snapshot()
    assert (
        reducer._apply_context_sample(
            snap,
            _event(native=native),
        )
        is snap
    )


def test_late_event_decodes_before_it_is_suppressed(monkeypatch) -> None:
    existing = _state(observed_at="2026-07-24T00:00:10Z")
    snap = replace(reducer.initial_snapshot(), context_observations=(existing,))
    calls: list[str] = []

    def decode(payload, native):
        calls.append("decode")
        return _sample(native)

    monkeypatch.setattr(reducer, "context_sample_from_dict", decode)
    monkeypatch.setattr(
        reducer,
        "ContextObservationState",
        lambda **kwargs: pytest.fail("late event must not construct state"),
    )

    assert (
        reducer._apply_context_sample(
            snap,
            _event(occurred_at="2026-07-24T00:00:05Z"),
        )
        is snap
    )
    assert calls == ["decode"]


def test_malformed_late_event_keeps_codec_exception_order() -> None:
    existing = _state(observed_at="2026-07-24T00:00:10Z")
    snap = replace(reducer.initial_snapshot(), context_observations=(existing,))

    with pytest.raises(KeyError):
        reducer._apply_context_sample(
            snap,
            _event(occurred_at="2026-07-24T00:00:05Z"),
        )


def test_duplicate_pair_uses_first_for_late_gate() -> None:
    first = _state(observed_at="2026-07-24T00:00:10Z", generation=1)
    duplicate = _state(observed_at="2026-07-24T00:00:00Z", generation=2)
    snap = replace(
        reducer.initial_snapshot(),
        context_observations=(first, duplicate),
    )

    assert (
        reducer._apply_context_sample(
            snap,
            _event(
                occurred_at="2026-07-24T00:00:05Z",
                payload=_payload(generation=3),
            ),
        )
        is snap
    )


def test_successful_update_removes_all_pair_duplicates_and_appends() -> None:
    other = _state(native="session-other", observed_at="2026-07-24T00:00:20Z")
    first = _state(observed_at="2026-07-24T00:00:00Z", generation=1)
    duplicate = _state(observed_at="2026-07-24T00:00:10Z", generation=2)
    snap = replace(
        reducer.initial_snapshot(),
        context_observations=(first, other, duplicate),
    )

    updated = reducer._apply_context_sample(
        snap,
        _event(
            occurred_at="2026-07-24T00:00:05Z",
            payload=_payload(generation=3),
        ),
    )

    assert updated.context_observations[0] is other
    assert len(updated.context_observations) == 2
    assert updated.context_observations[1].generation == 3


def test_equal_timestamp_uses_fold_order_as_tiebreaker() -> None:
    observed_at = "2026-07-24T00:00:05Z"
    existing = _state(observed_at=observed_at, generation=1)
    snap = replace(reducer.initial_snapshot(), context_observations=(existing,))

    updated = reducer._apply_context_sample(
        snap,
        _event(
            occurred_at=observed_at,
            payload=_payload(generation=2),
        ),
    )

    assert updated.context_observations[0].generation == 2


def _payload(*, generation: int) -> dict[str, Any]:
    return {
        "main_or_subagent": "main",
        "turn_id": None,
        "request_identity": f"request-{generation}",
        "generation": generation,
        "input_tokens": None,
        "cache_creation_input_tokens": None,
        "cache_read_input_tokens": None,
        "output_tokens": None,
        "used_tokens": None,
        "effective_window_tokens": None,
        "ratio": None,
        "source": "test",
        "source_version": None,
        "confidence": "unavailable",
        "unavailable_reason": "missing_usage",
    }
