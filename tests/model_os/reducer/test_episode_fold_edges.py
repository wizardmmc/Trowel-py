from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from trowel_py.model_os import reducer
from trowel_py.model_os.types import EventEnvelope, EventKind, Provenance


_APPLY_CASES = [
    ("_apply_episode_status_change", EventKind.EPISODE_STATUS_CHANGED),
    ("_apply_episode_checkpoint", EventKind.EPISODE_CHECKPOINT_COMMITTED),
    ("_apply_episode_suspended", EventKind.EPISODE_SUSPENDED),
    ("_apply_episode_wait_resolved", EventKind.EPISODE_WAIT_RESOLVED),
    ("_apply_episode_reconcile_required", EventKind.EPISODE_RECONCILE_REQUIRED),
    ("_apply_episode_reconcile_resolved", EventKind.EPISODE_RECONCILE_RESOLVED),
]


def _event(
    kind: str,
    *,
    episode_id: str | None = "episode-1",
    payload: dict[str, Any] | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_id="event-episode-fold-edge",
        kind=kind,
        occurred_at="2026-07-24T00:00:00Z",
        source="test",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v0",
        payload=payload or {},
        episode_id=episode_id,
    )


def _episode(episode_id: str = "episode-1"):
    return reducer._episode_from_created(
        _event(
            EventKind.EPISODE_CREATED,
            episode_id=episode_id,
            payload={
                "episode_id": episode_id,
                "work_item_id": "work-1",
            },
        )
    )


@pytest.mark.parametrize(("facade_name", "kind"), _APPLY_CASES)
def test_unknown_episode_short_circuits_before_malformed_payload(
    facade_name: str,
    kind: str,
) -> None:
    snap = replace(reducer.initial_snapshot(), episodes=(_episode("other"),))
    event = _event(kind, episode_id="missing", payload={})

    assert getattr(reducer, facade_name)(snap, event) is snap


def test_malformed_duplicate_episodes_keep_first_find_and_replace_all() -> None:
    first = _episode()
    other = replace(_episode("other"), work_item_id="work-other")
    duplicate = replace(first, work_item_id="work-duplicate")
    snap = replace(
        reducer.initial_snapshot(),
        episodes=(first, other, duplicate),
    )
    new_state = replace(first, native_session_id="session-1")

    assert reducer._find_episode(snap, "episode-1") is first
    updated = reducer._replace_episode(snap, "episode-1", new_state)
    assert updated.episodes == (new_state, other, new_state)
    assert updated.episodes[0] is updated.episodes[2]
    assert reducer._replace_episode(snap, None, new_state) is snap


def test_duplicate_created_short_circuits_before_payload_validation() -> None:
    snap = replace(reducer.initial_snapshot(), episodes=(_episode(),))
    malformed_duplicate = _event(
        EventKind.EPISODE_CREATED,
        payload={"episode_id": "episode-1"},
    )

    assert reducer.reduce_event(snap, malformed_duplicate) is snap


def test_checkpoint_uses_event_id_and_only_changes_status_when_present() -> None:
    episode = _episode()
    snap = replace(reducer.initial_snapshot(), episodes=(episode,))
    event = _event(
        EventKind.EPISODE_CHECKPOINT_COMMITTED,
        payload={"version": "2", "payload_hash": "hash-2"},
    )

    updated = reducer._apply_episode_checkpoint(snap, event)

    assert updated.episodes[0].status is episode.status
    assert updated.episodes[0].last_snapshot_ref is not None
    assert updated.episodes[0].last_snapshot_ref.version == 2
    assert updated.episodes[0].last_snapshot_ref.committed_event_id == event.event_id


@pytest.mark.parametrize(
    "payload",
    [
        {"new_status": "closed", "version": 2, "payload_hash": ""},
        {"new_status": "closed", "version": None, "payload_hash": "hash-2"},
    ],
)
def test_reconcile_resolved_requires_complete_snapshot_identity(payload) -> None:
    episode = _episode()
    snap = replace(reducer.initial_snapshot(), episodes=(episode,))

    updated = reducer._apply_episode_reconcile_resolved(
        snap,
        _event(EventKind.EPISODE_RECONCILE_RESOLVED, payload=payload),
    )

    assert updated.episodes[0].last_snapshot_ref is None
