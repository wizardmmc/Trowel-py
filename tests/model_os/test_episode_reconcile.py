"""reconcile_required state + resolution tests (slice-087 pass 12, 13; H3).

``reconcile_required`` is a non-terminal BLOCKED state: the pending user
channel was lost (runtime restart, spike-083), so the system must not pretend
the old question is still answerable. It is exited ONLY by an explicit
``resolve_reconcile`` (a human/kernel decision).

codex H3 found four holes in the previous implementation:
1. ``mark_pending_channel_lost`` did not update the bound Task's waiting
   subtype, so the scheduler could not tell the wait is unrecoverable.
2. ``resolve_reconcile(close)`` skipped the recovery snapshot whenever the
   caller did not pass one — closing silently dropped the work现场.
3. ``resolve_reconcile(resume_safe)`` flipped only the Episode to ACTIVE,
   leaving the Task ``waiting_user``, the WorkItem ``SUSPENDED`` and the
   foreground empty — an unrecoverable contradiction.
4. ``confirmed_by`` was stored in the payload but the event kept
   ``MACHINE_OBSERVATION`` provenance, so audit could not tell a real human
   decision from a kernel self-report.

Decision 4A (user, 2026-07-21): resume_safe goes via ``suspended_ready`` then
the existing ``activate_suspended_episode`` — reusing the foreground CAS +
Task/WorkItem restoration instead of duplicating it.
"""

from __future__ import annotations

import pytest

from trowel_py.model_os.store import EpisodeCommandError, ModelOsStore
from trowel_py.model_os.types import (
    EpisodeStatus,
    EventKind,
    Provenance,
    ReconcileReason,
    TaskStatus,
    WaitingSubtype,
    WorkItemStatus,
)
from tests.model_os._episode_helpers import (
    FakeClock,
    activate_episode,
    make_running_task_episode,
)


def _park_in_reconcile(store, episode, lease, task, *, monkeypatch) -> None:
    """Drive a Task-bound ACTIVE episode through suspend + channel-lost into
    reconcile_required."""

    from trowel_py.model_os.types import PendingDescriptor

    store.suspend_episode(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        pending=PendingDescriptor(
            kind=WaitingSubtype.INPUT,
            native_generation="gen-1",
            correlation_id="corr-1",
            cause="need input",
            posed_at="2026-07-21T00:00:05Z",
        ),
    )
    store.mark_pending_channel_lost(
        episode.episode_id, reason=ReconcileReason.REQUIRES_USER_RESTART
    )


# ----------------------------------------- mark_pending_channel_lost (H3.1) ---


def test_mark_lost_sets_task_waiting_subtype_to_requires_user_restart(
    store: ModelOsStore, monkeypatch
) -> None:
    """H3.1: when the pending channel is lost, the bound Task's waiting
    condition must carry subtype ``requires_user_restart`` so the 095 matcher
    + scheduler do NOT try to auto-resume a dead wait."""

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, task, _ = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    _park_in_reconcile(store, episode, lease, task, monkeypatch=monkeypatch)

    snap = store.read_snapshot()
    task_state = next(t for t in snap.tasks if t.task_id == task.task_id)
    assert task_state.status == TaskStatus.WAITING_USER
    assert task_state.waiting_condition is not None
    assert (
        task_state.waiting_condition.subtype == WaitingSubtype.REQUIRES_USER_RESTART
    )


def test_mark_lost_only_from_suspended_states(
    store: ModelOsStore, monkeypatch
) -> None:
    """pass 12: channel-lost is only meaningful for a pending (suspended) or
    suspended_ready Episode. Marking an ACTIVE episode lost must refuse."""

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _, _ = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    with pytest.raises(EpisodeCommandError):
        store.mark_pending_channel_lost(
            episode.episode_id, reason=ReconcileReason.REQUIRES_USER_RESTART
        )


# --------------------------------------------------- resolve close (H3.2) ---


def test_resolve_close_always_produces_recovery_snapshot(
    store: ModelOsStore, monkeypatch
) -> None:
    """H3.2: ``resolve_reconcile(close)`` must ALWAYS leave a recovery_partial
    snapshot behind, even when the caller passes no explicit snapshot. The
    previous code silently closed without one, dropping the work现场 that 090
    needs to start fresh."""

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, task, _ = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    _park_in_reconcile(store, episode, lease, task, monkeypatch=monkeypatch)

    store.resolve_reconcile(
        episode.episode_id, decision="close", confirmed_by="human-1"
    )

    snap = store.read_snapshot()
    ep = snap.episode_by_id(episode.episode_id)
    assert ep.status == EpisodeStatus.CLOSED
    assert ep.last_snapshot_ref is not None, (
        "close must leave a recovery snapshot ref so 090 can resume from it"
    )
    recovery = store.read_episode_snapshot(ep.last_snapshot_ref)
    assert recovery.source.value == "recovery_partial"


def test_resolve_close_does_not_emit_unfenced_checkpoint_committed(
    store: ModelOsStore, monkeypatch
) -> None:
    """C1 corollary: the close path must NOT write an unfenced
    ``episode.checkpoint_committed`` event (that kind is fenced). The snapshot
    info rides on ``episode.reconcile_resolved`` instead, which is an
    externally-driven (unfenced) kind."""

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, task, _ = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    _park_in_reconcile(store, episode, lease, task, monkeypatch=monkeypatch)

    store.resolve_reconcile(
        episode.episode_id, decision="close", confirmed_by="human-1"
    )

    # any checkpoint_committed event for this episode must have come only from
    # fenced cooperative paths earlier (there were none here)
    ck_events = [
        ev for _, ev in store.list_events()
        if ev.kind == EventKind.EPISODE_CHECKPOINT_COMMITTED
        and ev.episode_id == episode.episode_id
    ]
    assert ck_events == [], (
        "close must not emit checkpoint_committed; snapshot info rides on "
        "reconcile_resolved"
    )


def test_resolve_close_releases_ownership_lease(
    store: ModelOsStore, monkeypatch
) -> None:
    """A closed Episode must not keep an active ownership lease."""

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, task, _ = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    _park_in_reconcile(store, episode, lease, task, monkeypatch=monkeypatch)

    store.resolve_reconcile(
        episode.episode_id, decision="close", confirmed_by="human-1"
    )
    row = store._read_episode_lease_row(episode.episode_id)
    assert row is None or row["released_at"] is not None


def test_resolve_records_user_decision_provenance(
    store: ModelOsStore, monkeypatch
) -> None:
    """H3.4: ``resolve_reconcile`` is a HUMAN decision; its event must carry
    ``USER_DECISION`` provenance so audit can tell it from a kernel self-
    report."""

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, task, _ = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    _park_in_reconcile(store, episode, lease, task, monkeypatch=monkeypatch)

    store.resolve_reconcile(
        episode.episode_id, decision="close", confirmed_by="human-1"
    )
    resolved_events = [
        ev for _, ev in store.list_events()
        if ev.kind == EventKind.EPISODE_RECONCILE_RESOLVED
        and ev.episode_id == episode.episode_id
    ]
    assert len(resolved_events) == 1
    assert resolved_events[0].provenance == Provenance.USER_DECISION
    assert resolved_events[0].payload.get("confirmed_by") == "human-1"


# ------------------------------------------------- resolve resume_safe (H3.3) ---


def test_resolve_resume_safe_goes_via_suspended_ready(
    store: ModelOsStore, monkeypatch
) -> None:
    """Decision 4A + H3.3: resume_safe lands the Episode in ``suspended_ready``
    (NOT active directly) and brings the Task to ``ready`` + WorkItem to
    ``READY``. The foreground is NOT claimed here — that is activate's job."""

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, task, work_item_id = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    _park_in_reconcile(store, episode, lease, task, monkeypatch=monkeypatch)

    store.resolve_reconcile(
        episode.episode_id, decision="resume_safe", confirmed_by="human-1"
    )
    snap = store.read_snapshot()
    ep = snap.episode_by_id(episode.episode_id)
    assert ep.status == EpisodeStatus.SUSPENDED_READY
    task_state = next(t for t in snap.tasks if t.task_id == task.task_id)
    assert task_state.status == TaskStatus.READY
    wi = next(w for w in snap.work_items if w.work_item_id == work_item_id)
    assert wi.status == WorkItemStatus.READY
    # foreground NOT claimed at resume_safe
    assert store.read_foreground_task_id() is None


def test_resume_safe_then_activate_reaches_active(
    store: ModelOsStore, monkeypatch
) -> None:
    """End-to-end resume: resume_safe → suspended_ready, then
    activate_suspended_episode → active (Task running, foreground reclaimed)."""

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, task, _ = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    _park_in_reconcile(store, episode, lease, task, monkeypatch=monkeypatch)

    store.resolve_reconcile(
        episode.episode_id, decision="resume_safe", confirmed_by="human-1"
    )
    store.activate_suspended_episode(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
    )
    snap = store.read_snapshot()
    assert snap.episode_by_id(episode.episode_id).status == EpisodeStatus.ACTIVE
    task_state = next(t for t in snap.tasks if t.task_id == task.task_id)
    assert task_state.status == TaskStatus.RUNNING
    assert store.read_foreground_task_id() == task.task_id


def test_resolve_reconcile_only_from_reconcile_required(
    store: ModelOsStore, monkeypatch
) -> None:
    """reconcile_required is the only legal source for resolve_reconcile."""

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _, _ = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    # still ACTIVE — resolve must refuse
    with pytest.raises(EpisodeCommandError):
        store.resolve_reconcile(
            episode.episode_id, decision="close", confirmed_by="human-1"
        )
