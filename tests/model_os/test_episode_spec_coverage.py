"""Spec pass-criteria coverage that earlier test files missed (opus round 3).

- pass 5 (write side): snapshot INSERT + checkpoint event are atomic — if the
  fenced event fails mid-tx (e.g. the lease expired between the snapshot INSERT
  and the fencing check), the snapshot row must roll back too.
- pass 8 (no bloat): three sequential cooperative snapshots on the same Task do
  not nest / grow linearly — previous content is referenced, not copied.
- pass 12 (passive defense): once an Episode is ``reconcile_required``, the
  normal resume path (resolve_episode_wait / activate_suspended_episode) is
  blocked; the ONLY exit is ``resolve_reconcile``.
- pass 16 (one WorkItem ↔ one Episode): slice-087 records the expectation via
  ``non_terminal_episodes_for_work_item``; enforcement lands in 090.
"""

from __future__ import annotations

import pytest

from trowel_py.model_os.store import (
    EpisodeCommandError,
    ModelOsStore,
    StaleWriterRejected,
)
from trowel_py.model_os.types import (
    EpisodeStatus,
    EventKind,
    PendingDescriptor,
    ReconcileReason,
    WaitingSubtype,
)

from tests.model_os._episode_helpers import (
    FakeClock,
    activate_episode,
    make_cooperative_snapshot,
    make_running_system_episode,
    make_running_task_episode,
)


def _input_pending(corr: str = "corr-1") -> PendingDescriptor:
    return PendingDescriptor(
        kind=WaitingSubtype.INPUT,
        native_generation="g1",
        correlation_id=corr,
        cause="need input",
        posed_at="2026-07-21T00:00:05Z",
    )


# --------------------------------------------------------------- pass 5 ---


def test_pass5_checkpoint_atomic_when_fence_fails_mid_tx(
    store: ModelOsStore, monkeypatch
) -> None:
    """pass 5: the snapshot row INSERT and the fenced checkpoint event share
    one transaction. If the fenced write fails (lease expired between the
    snapshot INSERT and the fencing check), the snapshot row must roll back —
    the previous valid version is retained and no half-state survives."""

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _ = make_running_system_episode(store, ttl_seconds=60)
    activate_episode(store, episode.episode_id, lease)
    store.request_yield(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        reason="done",
    )
    # expire the lease (no takeover). commit_checkpoint will INSERT the snapshot
    # row, THEN the fenced event runs the ownership check → StaleWriterRejected.
    clock.advance(61)

    with pytest.raises(StaleWriterRejected):
        store.commit_checkpoint(
            episode.episode_id,
            expected_lease_id=lease.lease_id,
            expected_owner=lease.owner,
            expected_token=lease.fencing_token,
            snapshot=make_cooperative_snapshot(),
            checkpoint_key="ck-atomic",
        )
    # the snapshot row must NOT have landed (atomic rollback)
    row = store._conn.execute(
        "SELECT COUNT(*) AS n FROM episode_snapshots "
        "WHERE episode_id=? AND checkpoint_key=?",
        (episode.episode_id, "ck-atomic"),
    ).fetchone()
    assert int(row["n"]) == 0, (
        "snapshot row must roll back when the fenced event fails in the same tx"
    )
    # the late_write audit DID land (M1: durable across the rollback)
    audits = [
        ev for _, ev in store.list_events()
        if ev.kind == EventKind.LATE_WRITE_REJECTED
        and ev.episode_id == episode.episode_id
    ]
    assert len(audits) == 1


# --------------------------------------------------------------- pass 8 ---


def _one_episode_cycle(store, task, lease_owner, ttl, prev_ref, judgment, key):
    """Start a fresh Episode on the Task, drive it to CLOSED, return its
    checkpoint SnapshotRef."""

    episode, lease = store.start_episode(
        work_item_id=task.primary_work_item_id,
        owner=lease_owner,
        ttl_seconds=ttl,
        idempotency_key=key,
        task_id=task.task_id,
        previous_snapshot_ref=prev_ref,
    )
    activate_episode(store, episode.episode_id, lease)
    store.request_yield(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        reason="done",
    )
    ref = store.commit_checkpoint(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        snapshot=make_cooperative_snapshot(current_judgment=judgment),
        checkpoint_key=f"ck-{key}",
    )
    store.close_episode(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
    )
    return ref


def test_pass8_three_episodes_do_not_bloat_snapshots(
    store: ModelOsStore, monkeypatch
) -> None:
    """pass 8 / spec line 225: three sequential Episodes on the same Task must
    not nest or linearly bloat the snapshot payload. A previous snapshot is
    referenced (base_snapshot_ref / previous_snapshot_ref), never copied into
    the next payload."""

    clock = FakeClock()
    clock.install(monkeypatch)
    episode0, lease0, task, _ = make_running_task_episode(
        store, ttl_seconds=300, idempotency_key="ep-seed"
    )
    activate_episode(store, episode0.episode_id, lease0)
    # close the seed episode so the WorkItem is free for the test cycles
    store.request_yield(
        episode0.episode_id,
        expected_lease_id=lease0.lease_id,
        expected_owner=lease0.owner,
        expected_token=lease0.fencing_token,
        reason="seed",
    )
    store.commit_checkpoint(
        episode0.episode_id,
        expected_lease_id=lease0.lease_id,
        expected_owner=lease0.owner,
        expected_token=lease0.fencing_token,
        snapshot=make_cooperative_snapshot(current_judgment="seed"),
        checkpoint_key="ck-seed",
    )
    store.close_episode(
        episode0.episode_id,
        expected_lease_id=lease0.lease_id,
        expected_owner=lease0.owner,
        expected_token=lease0.fencing_token,
    )

    ref1 = _one_episode_cycle(
        store, task, "runner-A", 300, None, "judgment-ONE", "ep-1"
    )
    ref2 = _one_episode_cycle(
        store, task, "runner-A", 300, ref1, "judgment-TWO", "ep-2"
    )
    ref3 = _one_episode_cycle(
        store, task, "runner-A", 300, ref2, "judgment-THREE", "ep-3"
    )

    snap3 = store.read_episode_snapshot(ref3)
    payload3_json = store._conn.execute(
        "SELECT payload_json FROM episode_snapshots "
        "WHERE episode_id=? AND version=?",
        (ref3.episode_id, ref3.version),
    ).fetchone()["payload_json"]
    # the latest snapshot carries its OWN judgment, not the older ones
    assert "judgment-THREE" in snap3.current_judgment
    assert "judgment-ONE" not in payload3_json, (
        "snapshot must not nest earlier snapshots' content (anti-bloat)"
    )
    assert "judgment-TWO" not in payload3_json
    # sizes are bounded — no linear growth from copying predecessors
    sizes = [
        int(
            store._conn.execute(
                "SELECT length(payload_json) AS L FROM episode_snapshots "
                "WHERE episode_id=? AND version=?",
                (r.episode_id, r.version),
            ).fetchone()["L"]
        )
        for r in (ref1, ref2, ref3)
    ]
    assert max(sizes) <= min(sizes) * 2, (
        f"snapshot sizes grew non-linearly: {sizes}"
    )


# --------------------------------------------------------------- pass 12 ---


def test_pass12_reconcile_required_blocks_normal_resume(
    store: ModelOsStore, monkeypatch
) -> None:
    """pass 12 (passive defense): an Episode in ``reconcile_required`` cannot
    sneak back to ACTIVE via the normal resume path. Both resolve_episode_wait
    and activate_suspended_episode refuse (wrong source state) — the ONLY exit
    is the explicit ``resolve_reconcile`` command."""

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, task, _ = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    store.suspend_episode(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        pending=_input_pending(),
    )
    store.mark_pending_channel_lost(
        episode.episode_id, reason=ReconcileReason.REQUIRES_USER_RESTART
    )
    assert (
        store.read_snapshot().episode_by_id(episode.episode_id).status
        == EpisodeStatus.RECONCILE_REQUIRED
    )

    # resolve_episode_wait refuses (source must be suspended_waiting_*)
    with pytest.raises(EpisodeCommandError):
        store.resolve_episode_wait(
            episode.episode_id, answer_correlation_id="corr-1"
        )
    # activate_suspended_episode refuses (source must be suspended_ready)
    with pytest.raises(EpisodeCommandError):
        store.activate_suspended_episode(
            episode.episode_id,
            expected_lease_id=lease.lease_id,
            expected_owner=lease.owner,
            expected_token=lease.fencing_token,
        )
    # state unchanged — still blocked
    assert (
        store.read_snapshot().episode_by_id(episode.episode_id).status
        == EpisodeStatus.RECONCILE_REQUIRED
    )


# --------------------------------------------------------------- pass 16 ---


def test_pass16_non_terminal_episodes_for_work_item_records_expectation(
    store: ModelOsStore, monkeypatch
) -> None:
    """pass 16 / spec line 250: a WorkItem should have at most one non-terminal
    Episode at a time. slice-087 does NOT enforce this (enforcement lands in
    090's 接力 / fresh-start logic); the Store exposes
    ``non_terminal_episodes_for_work_item`` so the expectation is recorded and
    090 has the guard already shaped. This test pins the current (087) shape:
    two non-terminal Episodes on one WorkItem are observable via the helper."""

    clock = FakeClock()
    clock.install(monkeypatch)
    # one system WorkItem, two Episodes started on it (both non-terminal)
    episode1, _, work_item_id = make_running_system_episode(
        store, ttl_seconds=300, idempotency_key="ep-A"
    )
    episode2, _, _ = make_running_system_episode(
        store, ttl_seconds=300, idempotency_key="ep-B"
    )
    # NOTE: both bind to DIFFERENT freshly-created WorkItems via the helper.
    # To put two Episodes on the SAME WorkItem, start a second one explicitly.
    episode2_same_wi, _ = store.start_episode(
        work_item_id=work_item_id,
        owner="runner-A",
        ttl_seconds=300,
        idempotency_key="ep-same-wi",
        task_id=None,
    )

    snap = store.read_snapshot()
    non_terminal = snap.non_terminal_episodes_for_work_item(work_item_id)
    ids = {e.episode_id for e in non_terminal}
    # 087 records the expectation: the helper surfaces BOTH non-terminal
    # Episodes (episode1 + episode2_same_wi). 090 will turn this read into a
    # guard that refuses the second.
    assert episode1.episode_id in ids
    assert episode2_same_wi.episode_id in ids
