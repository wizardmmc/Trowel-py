from __future__ import annotations

from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import EventEnvelope, EventKind, Provenance


def test_unconfirmed_side_effect_replays_as_unknown(store: ModelOsStore) -> None:
    store.append_event(
        EventEnvelope(
            event_id="evt-side",
            kind=EventKind.SIDE_EFFECT_UNCONFIRMED,
            occurred_at="2026-07-21T00:00:00Z",
            source="kernel",
            provenance=Provenance.UNKNOWN,
            policy_version="v0",
            payload={
                "description": "ran git commit but process died before ack",
                "command_hash": "sha256:abc",
            },
            outcome="requires_reconcile",
        )
    )

    snap = store.read_snapshot()
    assert len(snap.unknown_actions) == 1
    action = snap.unknown_actions[0]
    assert action.reconcile_kind == "requires_reconcile"
    assert action.event_id == "evt-side"


def test_pending_channel_lost_replays_as_unknown(store: ModelOsStore) -> None:
    store.append_event(
        EventEnvelope(
            event_id="evt-pending",
            kind=EventKind.PENDING_CHANNEL_LOST,
            occurred_at="2026-07-21T00:00:00Z",
            source="kernel",
            provenance=Provenance.UNKNOWN,
            policy_version="v0",
            payload={"description": "approval channel lost on restart"},
            outcome="requires_user_restart",
        )
    )
    snap = store.read_snapshot()
    assert len(snap.unknown_actions) == 1
    assert snap.unknown_actions[0].reconcile_kind == "requires_user_restart"


def test_replay_does_not_mutate_journal(store: ModelOsStore) -> None:
    store.append_event(
        EventEnvelope(
            event_id="evt-x",
            kind=EventKind.NOTE,
            occurred_at="2026-07-21T00:00:00Z",
            source="t",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version="v0",
            payload={"i": 1},
        )
    )
    before = store.list_events()
    store.replay()
    store.read_snapshot()
    after = store.list_events()
    assert before == after
