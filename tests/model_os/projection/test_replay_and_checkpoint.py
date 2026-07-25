from __future__ import annotations

from dataclasses import replace

import pytest

from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import (
    DecisionDisposition,
    DecisionRecord,
    EventEnvelope,
    EventKind,
    Provenance,
)
from trowel_py.model_os.context_observer import (
    ContextConfidence,
    ContextSample,
    UnavailableReason,
)


def _note(index: int) -> EventEnvelope:
    return EventEnvelope(
        event_id=f"event.note.{index}",
        kind=EventKind.NOTE,
        occurred_at="2026-07-25T00:00:00Z",
        source="test",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v1",
        payload={"index": index},
    )


def _audit_decision(index: int) -> DecisionRecord:
    return DecisionRecord(
        decision_id=f"decision.audit.{index}",
        kind="route",
        disposition=DecisionDisposition.SHADOW,
        decided_at=f"2026-07-25T00:00:{index:02d}Z",
        signals={"refs": []},
        candidates=["fast", "deep"],
        choice="fast",
        reason="shadow_default_fast",
        policy_version="v1",
    )


def test_incremental_replay_requires_matching_base_and_tracks_both_watermarks(
    store: ModelOsStore,
) -> None:
    store.create_task_from_user_request(original_goal="goal", idempotency_key="task.1")
    store.append_decision(_audit_decision(1))
    base_boundary = store.journal_boundary()
    base = store.replay(through=base_boundary)
    store.append_event(_note(2))
    store.append_decision(_audit_decision(2))
    through = store.journal_boundary()

    full = store.replay(through=through)
    incremental = store.replay(base_snapshot=base, after=base_boundary, through=through)

    assert incremental == full
    assert full.last_seq == through.event_seq
    assert full.last_decision_seq == through.decision_seq
    with pytest.raises(ValueError):
        store.replay(after=base_boundary, through=through)
    with pytest.raises(ValueError):
        store.replay(
            base_snapshot=replace(base, last_decision_seq=base.last_decision_seq + 1),
            after=base_boundary,
            through=through,
        )


def test_checkpoint_is_deletable_cache_and_corruption_falls_back_to_journal(
    store: ModelOsStore,
) -> None:
    store.create_task_from_user_request(original_goal="goal", idempotency_key="task.1")
    store.append_decision(_audit_decision(1))
    rebuilt = store.rebuild_snapshot_projection()
    assert store.read_snapshot() == replace(
        rebuilt,
        active_leases=store.read_snapshot().active_leases,
        foreground_task_id=store.read_snapshot().foreground_task_id,
    )

    assert store._conn is not None
    store._conn.execute(
        "UPDATE projection_checkpoints SET state_json='{', state_hash='broken'"
    )
    store._conn.commit()
    assert store.read_snapshot() == store.replay()

    store._conn.execute("DELETE FROM projection_checkpoints")
    store._conn.commit()
    assert store.read_snapshot() == store.replay()


def test_checkpoint_never_caches_live_lease_or_foreground(store: ModelOsStore) -> None:
    task = store.create_task_from_user_request(
        original_goal="goal", idempotency_key="task.1"
    )
    store.rebuild_snapshot_projection()
    lease = store.acquire_lease(
        resource_type="episode",
        resource_id="episode.1",
        owner="runner.1",
        ttl_seconds=60,
    )
    store.promote_to_warm(task.task_id)
    store.claim_foreground(task.task_id)

    current = store.read_snapshot()
    assert [item.lease_id for item in current.active_leases] == [lease.lease_id]
    assert current.foreground_task_id == task.task_id
    assert store._conn is not None
    state_json = store._conn.execute(
        "SELECT state_json FROM projection_checkpoints"
    ).fetchone()["state_json"]
    assert lease.lease_id not in state_json
    assert '"foreground_task_id":null' in state_json


def test_checkpoint_write_failure_keeps_previous_checkpoint(
    store: ModelOsStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.append_event(_note(1))
    previous = store.rebuild_snapshot_projection()
    store.append_event(_note(2))

    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected checkpoint failure")

    monkeypatch.setattr(store, "_insert_projection_checkpoint", fail)
    with pytest.raises(RuntimeError, match="injected checkpoint failure"):
        store.rebuild_snapshot_projection()

    assert store.read_snapshot() == store.replay()
    assert store._conn is not None
    row = store._conn.execute(
        "SELECT event_seq, decision_seq FROM projection_checkpoints"
    ).fetchone()
    assert (row["event_seq"], row["decision_seq"]) == (
        previous.last_seq,
        previous.last_decision_seq,
    )


def test_checkpoint_tail_reader_does_not_reload_prefix(
    store: ModelOsStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert store._conn is not None
    with store._conn:
        for index in range(1000):
            store._conn.execute(
                "INSERT INTO events (event_id, kind, occurred_at, source, provenance, "
                "policy_version, payload, payload_hash) VALUES (?, 'note', ?, 'test', "
                "'machine_observation', 'v1', '{}', 'sha256:empty')",
                (f"event.bulk.{index}", f"{index:08d}"),
            )
    store.rebuild_snapshot_projection()
    for index in range(1000, 1010):
        store.append_event(_note(index))

    original = store._read_event_range
    counts: list[int] = []

    def counted(*args: object, **kwargs: object):
        rows = original(*args, **kwargs)
        counts.append(len(rows))
        return rows

    monkeypatch.setattr(store, "_read_event_range", counted)
    snapshot = store.read_snapshot()

    assert snapshot.last_seq == 1010
    assert sum(counts) == 10


def test_context_projection_json_roundtrip(store: ModelOsStore) -> None:
    sample = ContextSample(
        native_session_id="session.1",
        main_or_subagent="main",
        turn_id="turn.1",
        request_identity="request.1",
        generation=2,
        input_tokens=100,
        cache_creation_input_tokens=20,
        cache_read_input_tokens=30,
        output_tokens=10,
        used_tokens=160,
        effective_window_tokens=200_000,
        ratio=0.0008,
        source="cc",
        source_version="2.1.197",
        confidence=ContextConfidence.RELIABLE,
        unavailable_reason=UnavailableReason.NONE,
    )
    store.record_context_sample(
        sample,
        episode_id=None,
        occurred_at="2026-07-25T00:00:00Z",
    )
    rebuilt = store.rebuild_snapshot_projection()

    assert store.read_snapshot() == rebuilt
    assert store.read_snapshot().context_observations[0].latest_sample == sample
