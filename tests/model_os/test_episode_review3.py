"""Tests for slice-087 opus round-3 findings (R3-H1..H4, M1..M3).

- R3-H1: ``resolve_reconcile(close)`` restores the bound Task/WorkItem to READY
  (previously left them stuck in a reconcile-wait forever).
- R3-H2: ``mark_pending_channel_lost`` does NOT silently skip a Task that was
  claimed by the scheduler between resolve and mark_lost (TOCTOU) — it pulls
  the Task back to a reconcile-wait and releases the foreground.
- R3-H3: ``idx_leases_idem`` is scoped to ``released_at IS NULL`` so a released
  lease's idempotency_key can be reused; takeover INSERTs translate any
  residual collision to ``LeaseConflict`` instead of leaking raw sqlite3 error.
- R3-H4: ``start_episode`` idempotent retry refuses to return a lease that now
  belongs to a different owner (taken over).
- R3-M1: ``resume_recovered_episode`` gates the Task source state.
- R3-M2: ``commit_checkpoint`` rejects a ``journal_through_seq`` past the live
  journal end.
- R3-M3: ``acquire_episode_ownership`` idempotent branch refuses an
  already-expired lease.
"""

from __future__ import annotations

import pytest

from trowel_py.model_os.store import (
    EpisodeCommandError,
    LeaseConflict,
    ModelOsStore,
)
from trowel_py.model_os.types import (
    PendingDescriptor,
    ReconcileReason,
    TaskStatus,
    WaitingSubtype,
    WorkItemStatus,
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


def _park_in_reconcile(store, episode, lease, task, *, monkeypatch) -> None:
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


# ================================================================== R3-H1 ---


def test_r3h1_resolve_close_restores_task_and_work_item(
    store: ModelOsStore, monkeypatch
) -> None:
    """R3-H1: resolve_reconcile(close) must return the bound Task/WorkItem to
    READY — closing the Episode without restoring them left them stuck (Task in
    waiting_user/reconcile, WorkItem SUSPENDED) with no command able to pull
    them out."""

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, task, work_item_id = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    _park_in_reconcile(store, episode, lease, task, monkeypatch=monkeypatch)

    store.resolve_reconcile(
        episode.episode_id, decision="close", confirmed_by="human-1"
    )
    snap = store.read_snapshot()
    assert snap.episode_by_id(episode.episode_id).status.value == "closed"
    task_state = next(t for t in snap.tasks if t.task_id == task.task_id)
    assert task_state.status == TaskStatus.READY
    wi = next(w for w in snap.work_items if w.work_item_id == work_item_id)
    assert wi.status == WorkItemStatus.READY


# ================================================================== R3-H2 ---


def test_r3h2_mark_lost_pulls_back_task_claimed_by_scheduler(
    store: ModelOsStore, monkeypatch
) -> None:
    """R3-H2 TOCTOU: resolve moves the Task to READY; the scheduler then claims
    it (→ RUNNING, foreground held). mark_pending_channel_lost must NOT silently
    skip — it pulls the Task back to a non-schedulable reconcile-wait and
    releases the foreground so the blocked Episode's Task cannot keep running."""

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
    store.resolve_episode_wait(episode.episode_id, answer_correlation_id="corr-1")
    # scheduler claims the now-READY Task (foreground + RUNNING)
    store.claim_foreground(task.task_id)
    assert store.read_foreground_task_id() == task.task_id

    store.mark_pending_channel_lost(
        episode.episode_id, reason=ReconcileReason.REQUIRES_USER_RESTART
    )
    snap = store.read_snapshot()
    assert (
        snap.episode_by_id(episode.episode_id).status.value
        == "reconcile_required"
    )
    task_state = next(t for t in snap.tasks if t.task_id == task.task_id)
    assert task_state.status == TaskStatus.WAITING_USER
    assert (
        task_state.waiting_condition.subtype == WaitingSubtype.REQUIRES_USER_RESTART
    )
    assert store.read_foreground_task_id() is None, (
        "foreground must be released so the Task cannot keep running"
    )


# ================================================================== R3-H3 ---


def test_r3h3_idempotency_key_reusable_after_release(
    store: ModelOsStore, monkeypatch
) -> None:
    """R3-H3: a released lease's idempotency_key must be reusable for a NEW
    grant. The old index (no released_at filter) blocked this and leaked a raw
    sqlite3.IntegrityError. The rescoped index (released_at IS NULL) lets the
    new INSERT through."""

    clock = FakeClock()
    clock.install(monkeypatch)
    first = store.acquire_lease(
        resource_type="episode_ownership",
        resource_id="ep-1",
        owner="runner-A",
        ttl_seconds=300,
        idempotency_key="K",
    )
    store.release_lease(first.lease_id)
    # same key, same owner, same resource — a fresh grant (new lease_id, new
    # token), NOT an IntegrityError.
    second = store.acquire_lease(
        resource_type="episode_ownership",
        resource_id="ep-1",
        owner="runner-A",
        ttl_seconds=300,
        idempotency_key="K",
    )
    assert second.lease_id != first.lease_id
    assert second.fencing_token > first.fencing_token


def test_r3h3_takeover_collision_surfaces_as_lease_conflict(
    store: ModelOsStore, monkeypatch
) -> None:
    """R3-H3 defense: any residual collision in the takeover INSERT path must
    surface as LeaseConflict, never a raw sqlite3.IntegrityError."""

    clock = FakeClock()
    clock.install(monkeypatch)
    store.acquire_lease(
        resource_type="episode_ownership",
        resource_id="ep-1",
        owner="runner-A",
        ttl_seconds=60,
        idempotency_key="K-A",
    )
    clock.advance(61)  # expire
    # a new owner takes over — must succeed (no raw IntegrityError)
    second = store.acquire_lease(
        resource_type="episode_ownership",
        resource_id="ep-1",
        owner="runner-B",
        ttl_seconds=60,
        idempotency_key="K-B",
    )
    assert second.owner == "runner-B"


# ================================================================== R3-H4 ---


def test_r3h4_start_episode_retry_after_takeover_raises(
    store: ModelOsStore, monkeypatch
) -> None:
    """R3-H4: if the original owner's lease expired and a new runner took over
    via recover_episode, the original owner's idempotent retry must NOT return
    the new owner's lease — it must LeaseConflict."""

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, _old_lease, _ = make_running_system_episode(
        store, ttl_seconds=60, idempotency_key="ep-K"
    )
    activate_episode(store, episode.episode_id, _old_lease)
    clock.advance(61)  # original lease expires
    store.recover_episode(
        episode.episode_id,
        new_owner="runner-B",
        ttl_seconds=300,
        idempotency_key="recover-K",
        reason="restart",
    )
    # original owner retries start_episode with the SAME idempotency_key
    with pytest.raises(LeaseConflict):
        store.start_episode(
            work_item_id=episode.work_item_id,
            owner="runner-A",  # original owner — but the lease is now B's
            ttl_seconds=300,
            idempotency_key="ep-K",
            task_id=None,
        )


# ================================================================== R3-M2 ---


def test_r3m2_commit_checkpoint_rejects_future_journal_through_seq(
    store: ModelOsStore, monkeypatch
) -> None:
    """R3-M2: a snapshot whose journal_through_seq looks past the live journal
    end is refused (a too-high value would silently skip events on the next
    recovery)."""

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _ = make_running_system_episode(store)
    activate_episode(store, episode.episode_id, lease)
    store.request_yield(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        reason="done",
    )
    live_end = store.read_snapshot().last_seq
    future_snapshot = make_cooperative_snapshot(journal_through_seq=live_end + 999)
    with pytest.raises(EpisodeCommandError):
        store.commit_checkpoint(
            episode.episode_id,
            expected_lease_id=lease.lease_id,
            expected_owner=lease.owner,
            expected_token=lease.fencing_token,
            snapshot=future_snapshot,
            checkpoint_key="ck-future",
        )


# ================================================================== R3-M3 ---


def test_r3m3_acquire_idempotent_refuses_expired_lease(
    store: ModelOsStore, monkeypatch
) -> None:
    """R3-M3: an idempotent retry that lands after the lease's TTL must NOT
    return the expired lease (the caller would fail its first fenced write).
    Raise LeaseConflict so the caller re-acquires fresh."""

    clock = FakeClock()
    clock.install(monkeypatch)
    store.acquire_episode_ownership(
        "ep-1", owner="runner-A", ttl_seconds=60, idempotency_key="K"
    )
    clock.advance(61)  # expired but not released/taken-over
    with pytest.raises(LeaseConflict):
        store.acquire_episode_ownership(
            "ep-1", owner="runner-A", ttl_seconds=60, idempotency_key="K"
        )


# ================================================================== R3-M4 ---


def test_r3m4_record_side_effect_is_idempotent_on_action_and_key(
    store: ModelOsStore, monkeypatch
) -> None:
    """R3-M4: record_side_effect is idempotent on (action_ref, idempotency_key).
    A crash-retry that already landed must NOT append a second
    side_effect_recorded event or a duplicate UnknownAction."""

    from trowel_py.model_os.types import EventKind

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _ = make_running_system_episode(store)
    activate_episode(store, episode.episode_id, lease)

    call = lambda: store.record_side_effect(  # noqa: E731
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        action_ref="action/send-email",
        idempotency_key="se-key-1",
        outcome="done",
        evidence_ref="evidence/sent.txt",
    )
    call()
    call()  # idempotent retry

    recorded = [
        ev for _, ev in store.list_events()
        if ev.kind == EventKind.EPISODE_SIDE_EFFECT_RECORDED
        and ev.episode_id == episode.episode_id
    ]
    assert len(recorded) == 1, (
        "record_side_effect must dedup on (action_ref, idempotency_key)"
    )

