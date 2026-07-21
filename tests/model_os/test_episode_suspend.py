"""Suspend / resolve / activate tests for slice-087 (pass 10, 17, 18; H1, M5).

The two-phase resume is the heart of slice-087:
    active → suspend_episode → suspended_waiting_*   (release foreground)
            → resolve_episode_wait → suspended_ready  (answer arrived; do NOT
              claim foreground yet)
            → activate_suspended_episode → active      (scheduler won foreground)

codex findings covered here:
- H1: ``resolve_episode_wait`` must (a) verify the answer's correlation_id
  matches the pending descriptor, (b) move the Task ``waiting_user → ready``
  and the WorkItem ``SUSPENDED → READY``. The previous code wrote only the
  Episode event, so a Task-bound Episode got stuck: activate requires the Task
  to be READY and would always raise.
- M5: ``activate_suspended_episode`` must restore the WorkItem ``SUSPENDED →
  RUNNING`` on the no-Task (system WorkItem) branch too. The previous code
  gated the WorkItem restoration inside ``if task_id is not None``, so a system
  Episode ended up ACTIVE with a still-SUSPENDED WorkItem.

L1 (duplicate SUSPENDED event) is covered by counting work_item.status_changed
events after suspend.
"""

from __future__ import annotations

import pytest

from trowel_py.model_os.store import EpisodeCommandError, ModelOsStore
from trowel_py.model_os.types import (
    EpisodeStatus,
    EventKind,
    PendingDescriptor,
    TaskStatus,
    WaitingSubtype,
    WorkItemStatus,
)

from tests.model_os._episode_helpers import (
    FakeClock,
    activate_episode,
    make_running_system_episode,
    make_running_task_episode,
)


def _pending(
    *, kind: WaitingSubtype = WaitingSubtype.INPUT, correlation_id: str = "corr-1"
) -> PendingDescriptor:
    return PendingDescriptor(
        kind=kind,
        native_generation="gen-1",
        correlation_id=correlation_id,
        cause="需要用户回答",
        posed_at="2026-07-21T00:00:05Z",
    )


def _suspend_task_episode(store, episode, lease, *, kind=WaitingSubtype.INPUT, corr="corr-1"):
    store.suspend_episode(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        pending=_pending(kind=kind, correlation_id=corr),
    )


# ----------------------------------------------------- Task-bound two-phase ---


def test_task_bound_suspend_releases_foreground(
    store: ModelOsStore, monkeypatch
) -> None:
    """pass 10: a pending suspend must NOT keep the foreground. The Task goes
    to waiting_user and the foreground CAS releases in the same tx."""

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, task, _ = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    assert store.read_foreground_task_id() == task.task_id

    _suspend_task_episode(store, episode, lease)

    assert store.read_foreground_task_id() is None
    snap = store.read_snapshot()
    assert snap.episode_by_id(episode.episode_id).status == EpisodeStatus.SUSPENDED_WAITING_INPUT
    task_state = next(t for t in snap.tasks if t.task_id == task.task_id)
    assert task_state.status == TaskStatus.WAITING_USER
    assert task_state.waiting_condition.subtype == WaitingSubtype.INPUT
    assert task_state.waiting_condition.episode_id == episode.episode_id
    assert task_state.waiting_condition.correlation_id == "corr-1"


def test_resolve_moves_task_to_ready_and_work_item(
    store: ModelOsStore, monkeypatch
) -> None:
    """H1: resolve must move the Task ``waiting_user → ready`` AND the primary
    WorkItem ``SUSPENDED → READY``, not just flip the Episode. Without this the
    next activate dead-locks (it requires the Task READY)."""

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, task, work_item_id = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    _suspend_task_episode(store, episode, lease)

    store.resolve_episode_wait(
        episode.episode_id, answer_correlation_id="corr-1"
    )

    snap = store.read_snapshot()
    assert snap.episode_by_id(episode.episode_id).status == EpisodeStatus.SUSPENDED_READY
    task_state = next(t for t in snap.tasks if t.task_id == task.task_id)
    assert task_state.status == TaskStatus.READY
    work_item = next(w for w in snap.work_items if w.work_item_id == work_item_id)
    assert work_item.status == WorkItemStatus.READY


def test_resolve_does_not_claim_foreground(store: ModelOsStore, monkeypatch) -> None:
    """pass 10: the answer arriving must NOT immediately claim foreground.
    Foreground is claimed only at activate, by the scheduler (086: foreground
    ⇔ running; suspended_ready is not running)."""

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, task, _ = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    _suspend_task_episode(store, episode, lease)

    store.resolve_episode_wait(
        episode.episode_id, answer_correlation_id="corr-1"
    )
    assert store.read_foreground_task_id() is None


def test_resolve_rejects_wrong_correlation_id(
    store: ModelOsStore, monkeypatch
) -> None:
    """H1: the answer's correlation_id must match the pending descriptor. A
    mismatched answer must be refused so an unrelated/late answer cannot clear
    the real pending."""

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, task, _ = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    _suspend_task_episode(store, episode, lease, corr="corr-1")

    with pytest.raises(EpisodeCommandError):
        store.resolve_episode_wait(
            episode.episode_id, answer_correlation_id="WRONG"
        )
    # nothing changed
    snap = store.read_snapshot()
    assert snap.episode_by_id(episode.episode_id).status == EpisodeStatus.SUSPENDED_WAITING_INPUT
    task_state = next(t for t in snap.tasks if t.task_id == task.task_id)
    assert task_state.status == TaskStatus.WAITING_USER


def test_full_task_bound_resume_reaches_active(
    store: ModelOsStore, monkeypatch
) -> None:
    """End-to-end: suspend → resolve → activate brings a Task-bound Episode
    back to ACTIVE with the Task running and foreground reclaimed — and does
    NOT create a fresh native session (pass 10)."""

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, task, _ = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    _suspend_task_episode(store, episode, lease)

    store.resolve_episode_wait(episode.episode_id, answer_correlation_id="corr-1")
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


# ------------------------------------------- system WorkItem (no Task) ---


def test_system_episode_suspend_and_full_resume(
    store: ModelOsStore, monkeypatch
) -> None:
    """pass 17 + M5: a system WorkItem Episode (no Task) must suspend and
    resume through the same two-phase flow. activate must restore the WorkItem
    ``SUSPENDED → RUNNING`` even though there is no Task (the previous code
    gated WorkItem restoration behind ``if task_id is not None``)."""

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, work_item_id = make_running_system_episode(store)
    activate_episode(store, episode.episode_id, lease)

    store.suspend_episode(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        pending=_pending(),
    )
    snap = store.read_snapshot()
    assert snap.episode_by_id(episode.episode_id).status == EpisodeStatus.SUSPENDED_WAITING_INPUT
    wi = next(w for w in snap.work_items if w.work_item_id == work_item_id)
    assert wi.status == WorkItemStatus.SUSPENDED
    # no Task → foreground never held
    assert store.read_foreground_task_id() is None

    store.resolve_episode_wait(episode.episode_id, answer_correlation_id="corr-1")
    snap = store.read_snapshot()
    assert snap.episode_by_id(episode.episode_id).status == EpisodeStatus.SUSPENDED_READY
    wi = next(w for w in snap.work_items if w.work_item_id == work_item_id)
    assert wi.status == WorkItemStatus.READY

    store.activate_suspended_episode(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
    )
    snap = store.read_snapshot()
    assert snap.episode_by_id(episode.episode_id).status == EpisodeStatus.ACTIVE
    wi = next(w for w in snap.work_items if w.work_item_id == work_item_id)
    assert wi.status == WorkItemStatus.RUNNING, (
        "M5: system WorkItem must be RUNNING after activate, not left SUSPENDED"
    )


# ------------------------------------------- L1: no duplicate SUSPENDED ---


def test_suspend_emits_one_work_item_suspended_event(
    store: ModelOsStore, monkeypatch
) -> None:
    """L1: suspend must emit the WorkItem→SUSPENDED event exactly once. The
    previous code emitted it twice for a Task-bound Episode (once in suspend,
    once inside _set_waiting_in_tx), bloating the audit log."""

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, task, work_item_id = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)

    _suspend_task_episode(store, episode, lease)

    all_suspended = [
        ev
        for (_, ev) in store.list_events()
        if ev.kind == EventKind.WORK_ITEM_STATUS_CHANGED
        and ev.work_item_id == work_item_id
        and ev.payload.get("new_status") == WorkItemStatus.SUSPENDED.value
    ]
    assert len(all_suspended) == 1, (
        f"suspend should emit WorkItem→SUSPENDED once, got {len(all_suspended)}"
    )
