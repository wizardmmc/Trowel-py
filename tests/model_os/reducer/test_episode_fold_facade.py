from __future__ import annotations

import inspect
import pickle
import typing
from dataclasses import replace
from typing import Any

import pytest

from trowel_py import model_os
from trowel_py.model_os import episode_fold, reducer, store
from trowel_py.model_os.types import EventEnvelope, EventKind, Provenance


_APPLY_CASES = [
    ("_apply_episode_status_change", EventKind.EPISODE_STATUS_CHANGED),
    ("_apply_episode_checkpoint", EventKind.EPISODE_CHECKPOINT_COMMITTED),
    ("_apply_episode_suspended", EventKind.EPISODE_SUSPENDED),
    ("_apply_episode_wait_resolved", EventKind.EPISODE_WAIT_RESOLVED),
    ("_apply_episode_reconcile_required", EventKind.EPISODE_RECONCILE_REQUIRED),
    ("_apply_episode_reconcile_resolved", EventKind.EPISODE_RECONCILE_RESOLVED),
]

_REDUCE_CASES = [
    *[
        ("_apply_episode_status_change", kind)
        for kind in (
            EventKind.EPISODE_STATUS_CHANGED,
            EventKind.EPISODE_YIELD_REQUESTED,
            EventKind.EPISODE_CLOSED,
            EventKind.EPISODE_FAILED,
            EventKind.EPISODE_ACTIVATED,
            EventKind.EPISODE_RECOVERING,
        )
    ],
    *_APPLY_CASES[1:],
]


def _event(kind: str, payload: dict[str, Any] | None = None) -> EventEnvelope:
    return EventEnvelope(
        event_id="event-episode-fold",
        kind=kind,
        occurred_at="2026-07-24T00:00:00Z",
        source="test",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v0",
        payload=payload or {},
        episode_id="episode-1",
    )


def test_episode_fold_facades_keep_complete_contracts() -> None:
    expected = {
        "_episode_from_created": "(event: 'EventEnvelope') -> 'EpisodeState'",
        "_find_episode": (
            "(snap: 'Snapshot', episode_id: 'str | None') -> 'EpisodeState | None'"
        ),
        "_replace_episode": (
            "(snap: 'Snapshot', episode_id: 'str | None', "
            "new_state: 'EpisodeState') -> 'Snapshot'"
        ),
        "_pending_from_payload": "(p: 'dict[str, Any]') -> 'PendingDescriptor'",
        **{
            facade: "(snap: 'Snapshot', event: 'EventEnvelope') -> 'Snapshot'"
            for facade, _ in _APPLY_CASES
        },
    }

    for name, signature in expected.items():
        facade = getattr(reducer, name)
        implementation = (
            episode_fold.episode_from_created
            if name == "_episode_from_created"
            else getattr(episode_fold, name)
        )
        assert str(inspect.signature(facade)) == signature
        assert facade.__defaults__ is None
        assert facade.__kwdefaults__ is None
        assert facade.__module__ == reducer.__name__
        assert facade.__qualname__ == name
        assert facade is not implementation
        typing.get_type_hints(facade)


def test_episode_state_keeps_public_identity_and_pickle_path() -> None:
    state = reducer._episode_from_created(
        _event(
            EventKind.EPISODE_CREATED,
            {"episode_id": "episode-1", "work_item_id": "work-1"},
        )
    )

    assert model_os.EpisodeState is reducer.EpisodeState is store.EpisodeState
    assert reducer.WaitingSubtype is model_os.WaitingSubtype
    assert reducer.EpisodeState.__module__ == reducer.__name__
    assert pickle.loads(pickle.dumps(reducer.EpisodeState)) is reducer.EpisodeState
    assert pickle.loads(pickle.dumps(state)) == state


def test_created_facade_delegates_current_episode_state(monkeypatch) -> None:
    captured: dict[str, Any] = {}
    result = object()

    def implementation(*args, **kwargs):
        captured.update(args=args, kwargs=kwargs)
        return result

    event = _event(EventKind.EPISODE_CREATED)
    episode_state_factory = object()
    monkeypatch.setattr(reducer, "EpisodeState", episode_state_factory)
    monkeypatch.setattr(reducer, "_run_episode_from_created", implementation)

    assert reducer._episode_from_created(event) is result
    assert captured == {
        "args": (event,),
        "kwargs": {
            "episode_state_factory": episode_state_factory,
            "episode_status": reducer.EpisodeStatus,
        },
    }


@pytest.mark.parametrize(("facade_name", "kind"), _REDUCE_CASES)
def test_reduce_event_uses_current_apply_facade(
    monkeypatch,
    facade_name: str,
    kind: str,
) -> None:
    marker = object()
    monkeypatch.setattr(reducer, facade_name, lambda snap, event: marker)

    assert reducer.reduce_event(reducer.initial_snapshot(), _event(kind)) is marker


def test_reduce_event_uses_current_created_facade(monkeypatch) -> None:
    marker = object()
    monkeypatch.setattr(reducer, "_episode_from_created", lambda event: marker)
    snap = reducer.reduce_event(
        reducer.initial_snapshot(),
        _event(EventKind.EPISODE_CREATED, {"episode_id": "episode-1"}),
    )

    assert snap.episodes == (marker,)


@pytest.mark.parametrize(("facade_name", "kind"), _APPLY_CASES)
def test_apply_facades_resolve_find_episode_at_call_time(
    monkeypatch,
    facade_name: str,
    kind: str,
) -> None:
    episode = reducer._episode_from_created(
        _event(
            EventKind.EPISODE_CREATED,
            {"episode_id": "episode-1", "work_item_id": "work-1"},
        )
    )
    snap = replace(reducer.initial_snapshot(), episodes=(episode,))
    monkeypatch.setattr(reducer, "_find_episode", lambda snap, episode_id: None)

    assert getattr(reducer, facade_name)(snap, _event(kind)) is snap


def test_status_fold_resolves_dataclass_replace_at_call_time(monkeypatch) -> None:
    episode = reducer._episode_from_created(
        _event(
            EventKind.EPISODE_CREATED,
            {"episode_id": "episode-1", "work_item_id": "work-1"},
        )
    )
    snap = replace(reducer.initial_snapshot(), episodes=(episode,))
    replaced_types: list[type] = []

    def replace_spy(instance, **changes):
        replaced_types.append(type(instance))
        return replace(instance, **changes)

    monkeypatch.setattr(reducer, "replace", replace_spy)

    reducer._apply_episode_status_change(
        snap,
        _event(EventKind.EPISODE_STATUS_CHANGED, {"new_status": "active"}),
    )

    assert replaced_types == [reducer.EpisodeState, reducer.Snapshot]


def test_status_fold_resolves_replace_episode_at_call_time(monkeypatch) -> None:
    episode = reducer._episode_from_created(
        _event(
            EventKind.EPISODE_CREATED,
            {"episode_id": "episode-1", "work_item_id": "work-1"},
        )
    )
    snap = replace(reducer.initial_snapshot(), episodes=(episode,))
    marker = object()
    monkeypatch.setattr(
        reducer,
        "_replace_episode",
        lambda snap, episode_id, new_state: marker,
    )

    assert (
        reducer._apply_episode_status_change(
            snap,
            _event(EventKind.EPISODE_STATUS_CHANGED, {"new_status": "active"}),
        )
        is marker
    )


def test_suspended_fold_resolves_pending_decoder_at_call_time(monkeypatch) -> None:
    episode = reducer._episode_from_created(
        _event(
            EventKind.EPISODE_CREATED,
            {"episode_id": "episode-1", "work_item_id": "work-1"},
        )
    )
    snap = replace(reducer.initial_snapshot(), episodes=(episode,))
    pending = object()
    monkeypatch.setattr(reducer, "_pending_from_payload", lambda payload: pending)

    updated = reducer._apply_episode_suspended(
        snap,
        _event(
            EventKind.EPISODE_SUSPENDED,
            {"new_status": "suspended_waiting_input"},
        ),
    )

    assert updated.episodes[0].pending_descriptor is pending
