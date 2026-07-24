from __future__ import annotations

import inspect
import pickle
import typing
from dataclasses import replace
from typing import Any

from trowel_py.model_os import context_fold, reducer
from trowel_py.model_os.context_observer import (
    ContextConfidence,
    ContextSample,
    UnavailableReason,
)
from trowel_py.model_os.types import EventEnvelope, EventKind, Provenance


def _sample(native: str = "session-1", generation: int = 1) -> ContextSample:
    return ContextSample(
        native_session_id=native,
        main_or_subagent="main",
        turn_id="turn-1",
        request_identity="request-1",
        generation=generation,
        input_tokens=10,
        cache_creation_input_tokens=None,
        cache_read_input_tokens=None,
        output_tokens=2,
        used_tokens=12,
        effective_window_tokens=100,
        ratio=0.12,
        source="test",
        source_version="1",
        confidence=ContextConfidence.RELIABLE,
        unavailable_reason=UnavailableReason.NONE,
    )


def _event(
    payload: dict[str, Any] | None = None,
    *,
    native: str | None = "session-1",
    occurred_at: str = "2026-07-24T00:00:00Z",
) -> EventEnvelope:
    return EventEnvelope(
        event_id="event-context-fold",
        kind=EventKind.CONTEXT_SAMPLE_OBSERVED,
        occurred_at=occurred_at,
        source="test",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v0",
        payload=payload or {},
        episode_id="episode-1",
        native_session_id=native,
    )


def test_context_fold_facade_keeps_runtime_contract() -> None:
    facade = reducer._apply_context_sample

    assert str(inspect.signature(facade)) == (
        "(snap: 'Snapshot', event: 'EventEnvelope') -> 'Snapshot'"
    )
    assert facade.__defaults__ is None
    assert facade.__kwdefaults__ is None
    assert facade.__module__ == reducer.__name__
    assert facade.__qualname__ == "_apply_context_sample"
    assert facade is not context_fold.apply_context_sample
    typing.get_type_hints(facade)


def test_context_observation_state_keeps_reducer_pickle_path() -> None:
    state = reducer.ContextObservationState(
        episode_id="episode-1",
        native_session_id="session-1",
        generation=1,
        latest_sample=_sample(),
        observed_at="2026-07-24T00:00:00Z",
    )

    assert reducer.ContextObservationState.__module__ == reducer.__name__
    assert (
        pickle.loads(pickle.dumps(reducer.ContextObservationState))
        is reducer.ContextObservationState
    )
    assert pickle.loads(pickle.dumps(state)) == state


def test_reduce_event_uses_current_context_fold_facade(monkeypatch) -> None:
    marker = object()
    monkeypatch.setattr(reducer, "_apply_context_sample", lambda snap, event: marker)

    assert reducer.reduce_event(reducer.initial_snapshot(), _event()) is marker


def test_context_fold_resolves_decoder_and_state_factory_at_call_time(
    monkeypatch,
) -> None:
    sample = _sample()
    state = object()
    calls: list[tuple[Any, ...]] = []

    def decode(payload, native):
        calls.append(("decode", payload, native))
        return sample

    def state_factory(**kwargs):
        calls.append(("state", kwargs))
        return state

    monkeypatch.setattr(reducer, "context_sample_from_dict", decode)
    monkeypatch.setattr(reducer, "ContextObservationState", state_factory)
    payload = {"native_session_id": "payload-must-not-own-session"}

    updated = reducer._apply_context_sample(
        reducer.initial_snapshot(),
        _event(payload),
    )

    assert updated.context_observations == (state,)
    assert calls == [
        ("decode", payload, "session-1"),
        (
            "state",
            {
                "episode_id": "episode-1",
                "native_session_id": "session-1",
                "generation": 1,
                "latest_sample": sample,
                "observed_at": "2026-07-24T00:00:00Z",
            },
        ),
    ]


def test_context_fold_resolves_snapshot_replace_at_call_time(monkeypatch) -> None:
    replaced: list[Any] = []

    def replace_spy(instance, **changes):
        replaced.append(instance)
        return replace(instance, **changes)

    monkeypatch.setattr(
        reducer, "context_sample_from_dict", lambda payload, native: _sample()
    )
    monkeypatch.setattr(reducer, "replace", replace_spy)

    snap = reducer.initial_snapshot()
    reducer._apply_context_sample(snap, _event())

    assert replaced == [snap]
