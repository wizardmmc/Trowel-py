"""Tests for slice-087 codex round-2 findings (N1-N8).

These pin the second-round fixes:
- N1: ``append_decision_with_intent`` must refuse lifecycle kinds (no forge path).
- N2: ``commit_checkpoint`` resolves a same-key idempotent retry BEFORE the
  status gate, so a crash-after-COMMIT retry returns the original ref.
- N3: ``RECOVERING`` now has exits — ``resume_recovered_episode`` (→ACTIVE) and
  ``close_recovered_episode`` (→CLOSED, requires a recovery snapshot).
- N4: ``resolve_episode_wait`` rejects when the bound Task/WorkItem is not in
  the expected state, instead of silently skipping.
- N5: ``mark_pending_channel_lost`` from ``SUSPENDED_READY`` pulls the Task/
  WorkItem back to a non-schedulable reconcile-wait.
- N7: ``read_episode_snapshot`` requires the ref/row/event to agree PRECISELY
  on committed_event_id (a forged ref aimed at an unrelated event is refused).
- N8: ``start_episode`` refuses an INCUBATION WorkItem (which carries a
  task_id) started without a task_id.

N6 (preserve unknown side effects) is covered in ``test_episode_recovery.py``.
"""

from __future__ import annotations

import pytest

from trowel_py.model_os.store import (
    EpisodeCommandError,
    ModelOsStore,
)
from trowel_py.model_os.types import (
    DecisionRecord,
    EpisodeStatus,
    EventEnvelope,
    EventKind,
    PendingDescriptor,
    Provenance,
    ReconcileReason,
    TaskStatus,
    WaitingSubtype,
    WorkItemKind,
    WorkItemStatus,
)
from tests.model_os._episode_helpers import (
    FakeClock,
    activate_episode,
    make_cooperative_snapshot,
    make_running_system_episode,
    make_running_task_episode,
)


# ================================================================== N1 ---


def _decision(decision_id: str) -> DecisionRecord:
    return DecisionRecord(
        decision_id=decision_id,
        kind="route",
        decided_at="2026-07-21T00:00:00Z",
        signals={"usage_ratio": 0.8},
        candidates=["fast", "deep"],
        choice="deep",
        reason="validator failed",
        policy_version="v0",
    )


def test_n1_append_decision_with_intent_refuses_episode_lifecycle_kind(
    store: ModelOsStore,
) -> None:
    """N1 (CRITICAL): the public ``append_decision_with_intent`` must apply the
    same lifecycle gate as ``append_event``. Without it a caller could forge an
    ``episode.status_changed`` intent here and bypass every fencing gate — the
    journal would fold the forged event into authoritative state."""

    forged = EventEnvelope(
        event_id="forge.1",
        kind=EventKind.EPISODE_STATUS_CHANGED,
        occurred_at="2026-07-21T00:00:00Z",
        source="attacker",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v0",
        payload={"new_status": "closed"},
        episode_id="ep-x",
    )
    with pytest.raises(EpisodeCommandError):
        store.append_decision_with_intent(_decision("dec-1"), forged)


def test_n1_append_decision_with_intent_refuses_task_lifecycle_kind(
    store: ModelOsStore,
) -> None:
    """N1: the gate also covers Task lifecycle kinds."""

    forged = EventEnvelope(
        event_id="forge.2",
        kind=EventKind.TASK_COMPLETED,
        occurred_at="2026-07-21T00:00:00Z",
        source="attacker",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v0",
        payload={"confirmed_by": "x", "confirmation_provenance": "user_decision"},
        task_id="t-x",
    )
    with pytest.raises(Exception):  # TaskCommandError
        store.append_decision_with_intent(_decision("dec-2"), forged)


# ================================================================== N2 ---


def test_n2_commit_checkpoint_same_key_retry_returns_original_ref(
    store: ModelOsStore, monkeypatch
) -> None:
    """N2: a crash after COMMIT but before the response leaves the Episode in
    CHECKPOINTING. Retrying commit_checkpoint with the SAME key must return the
    original SnapshotRef — not fail the ACTIVE/YIELD_REQUESTED status gate."""

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
    first = store.commit_checkpoint(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        snapshot=make_cooperative_snapshot(),
        checkpoint_key="ck-retry",
    )
    # the Episode is now CHECKPOINTING; a same-key retry must be idempotent
    assert (
        store.read_snapshot().episode_by_id(episode.episode_id).status
        == EpisodeStatus.CHECKPOINTING
    )
    second = store.commit_checkpoint(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        snapshot=make_cooperative_snapshot(),
        checkpoint_key="ck-retry",
    )
    assert second.version == first.version
    assert second.committed_event_id == first.committed_event_id
    assert second.payload_hash == first.payload_hash


# ================================================================== N3 ---


def _episode_recovering(store, monkeypatch, *, ttl=60):
    """System Episode driven to RECOVERING via lease expiry + recover_episode."""
    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _ = make_running_system_episode(store, ttl_seconds=ttl)
    activate_episode(store, episode.episode_id, lease)
    clock.advance(ttl + 1)
    new_lease = store.recover_episode(
        episode.episode_id,
        new_owner="runner-B",
        ttl_seconds=300,
        idempotency_key="recover-n3",
        reason="restart",
    )
    return episode, new_lease


def test_n3_resume_recovered_episode_reaches_active(
    store: ModelOsStore, monkeypatch
) -> None:
    """N3: RECOVERING → ACTIVE via resume_recovered_episode after a recovery
    checkpoint. Previously RECOVERING had no exit, so a recovered Episode was
    stuck forever."""

    episode, new_lease = _episode_recovering(store, monkeypatch)
    store.checkpoint_recovery_partial(
        episode.episode_id,
        expected_lease_id=new_lease.lease_id,
        expected_owner=new_lease.owner,
        expected_token=new_lease.fencing_token,
        reason="post-crash",
        checkpoint_key="recover-ck-1",
    )
    store.resume_recovered_episode(
        episode.episode_id,
        expected_lease_id=new_lease.lease_id,
        expected_owner=new_lease.owner,
        expected_token=new_lease.fencing_token,
    )
    assert (
        store.read_snapshot().episode_by_id(episode.episode_id).status
        == EpisodeStatus.ACTIVE
    )


def test_n3_close_recovered_episode_reaches_closed(
    store: ModelOsStore, monkeypatch
) -> None:
    """N3: RECOVERING → CLOSED via close_recovered_episode, after a recovery
    snapshot was committed. The lease is released."""

    episode, new_lease = _episode_recovering(store, monkeypatch)
    store.checkpoint_recovery_partial(
        episode.episode_id,
        expected_lease_id=new_lease.lease_id,
        expected_owner=new_lease.owner,
        expected_token=new_lease.fencing_token,
        reason="post-crash",
        checkpoint_key="recover-ck-2",
    )
    store.close_recovered_episode(
        episode.episode_id,
        expected_lease_id=new_lease.lease_id,
        expected_owner=new_lease.owner,
        expected_token=new_lease.fencing_token,
    )
    ep = store.read_snapshot().episode_by_id(episode.episode_id)
    assert ep.status == EpisodeStatus.CLOSED
    row = store._read_episode_lease_row(episode.episode_id)
    assert row is None or row["released_at"] is not None


def test_n3_close_recovered_requires_recovery_snapshot(
    store: ModelOsStore, monkeypatch
) -> None:
    """N3: close_recovered_episode refuses if no recovery snapshot was
    committed — the close path must leave a recovery snapshot for 090."""

    episode, new_lease = _episode_recovering(store, monkeypatch)
    with pytest.raises(EpisodeCommandError):
        store.close_recovered_episode(
            episode.episode_id,
            expected_lease_id=new_lease.lease_id,
            expected_owner=new_lease.owner,
            expected_token=new_lease.fencing_token,
        )


# ================================================================== N4 ---


def test_n4_resolve_rejects_when_task_no_longer_waiting(
    store: ModelOsStore, monkeypatch
) -> None:
    """N4: if the bound Task is moved out of WAITING_USER while the Episode is
    suspended (e.g. cancelled), resolve must REFUSE rather than silently leave
    an inconsistent Episode=ready / Task=cancelled combo that activate can
    never consume."""

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, task, _ = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    store.suspend_episode(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        pending=PendingDescriptor(
            kind=WaitingSubtype.INPUT,
            native_generation="g1",
            correlation_id="corr-1",
            cause="need input",
            posed_at="2026-07-21T00:00:05Z",
        ),
    )
    # cancel the Task while the Episode is suspended — Task leaves WAITING_USER
    store.cancel_task(task.task_id, reason="abandoned")
    with pytest.raises(EpisodeCommandError):
        store.resolve_episode_wait(
            episode.episode_id, answer_correlation_id="corr-1"
        )
    # Episode untouched
    assert (
        store.read_snapshot().episode_by_id(episode.episode_id).status
        == EpisodeStatus.SUSPENDED_WAITING_INPUT
    )


# ================================================================== N5 ---


def test_n5_mark_lost_from_suspended_ready_unschedules_task(
    store: ModelOsStore, monkeypatch
) -> None:
    """N5: marking the channel lost from SUSPENDED_READY (where resolve had
    moved Task→READY, WorkItem→READY) must pull them back to a non-schedulable
    reconcile-wait — otherwise the scheduler can auto-run a Task whose Episode
    is blocked (violating 不自动 resume)."""

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, task, work_item_id = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    store.suspend_episode(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        pending=PendingDescriptor(
            kind=WaitingSubtype.INPUT,
            native_generation="g1",
            correlation_id="corr-1",
            cause="need input",
            posed_at="2026-07-21T00:00:05Z",
        ),
    )
    store.resolve_episode_wait(episode.episode_id, answer_correlation_id="corr-1")
    # Task + WorkItem are now READY
    store.mark_pending_channel_lost(
        episode.episode_id, reason=ReconcileReason.REQUIRES_USER_RESTART
    )
    snap = store.read_snapshot()
    assert (
        snap.episode_by_id(episode.episode_id).status
        == EpisodeStatus.RECONCILE_REQUIRED
    )
    task_state = next(t for t in snap.tasks if t.task_id == task.task_id)
    assert task_state.status == TaskStatus.WAITING_USER, (
        "Task must be pulled back to a non-schedulable wait while the Episode "
        "is reconcile_required"
    )
    assert (
        task_state.waiting_condition.subtype
        == WaitingSubtype.REQUIRES_USER_RESTART
    )
    wi = next(w for w in snap.work_items if w.work_item_id == work_item_id)
    assert wi.status == WorkItemStatus.SUSPENDED


# ================================================================== N7 ---


def test_n7_read_rejects_forged_committed_event_id(
    store: ModelOsStore, monkeypatch
) -> None:
    """N7: a SnapshotRef whose committed_event_id differs from the row's must
    be refused. The previous existence-only check let a forged ref aimed at an
    unrelated existing event pass."""

    from trowel_py.model_os.types import SnapshotRef

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
    ref = store.commit_checkpoint(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        snapshot=make_cooperative_snapshot(),
        checkpoint_key="ck-n7",
    )
    # seed an unrelated event, then forge a ref pointing at it
    store.append_event(
        EventEnvelope(
            event_id="note.unrelated",
            kind=EventKind.NOTE,
            occurred_at="2026-07-21T00:00:00Z",
            source="test",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version="v0",
            payload={"msg": "unrelated"},
        )
    )
    forged = SnapshotRef(
        episode_id=ref.episode_id,
        version=ref.version,
        committed_event_id="note.unrelated",  # exists, but wrong
        payload_hash=ref.payload_hash,
    )
    with pytest.raises(EpisodeCommandError):
        store.read_episode_snapshot(forged)


# ================================================================== N8 ---


def test_n8_start_episode_refuses_incubation_work_item_without_task_id(
    store: ModelOsStore,
) -> None:
    """N8: an INCUBATION WorkItem carries a task_id; starting it without that
    task_id must be refused (it is not a system WorkItem). The previous check
    only caught kind==TASK."""

    work_item = store.create_work_item(
        kind=WorkItemKind.INCUBATION,
        owner_ref="system",
        task_id="task-incubate-1",
        session_purpose=__import__(
            "trowel_py.model_os.types", fromlist=["SessionPurpose"]
        ).SessionPurpose.INCUBATION,
        memory_eligibility=__import__(
            "trowel_py.model_os.types", fromlist=["MemoryEligibility"]
        ).MemoryEligibility.INELIGIBLE,
    )
    with pytest.raises(EpisodeCommandError):
        store.start_episode(
            work_item_id=work_item.work_item_id,
            owner="runner-A",
            ttl_seconds=300,
            idempotency_key="ep-incubate",
            task_id=None,  # wrong — INCUBATION has a task_id
        )
