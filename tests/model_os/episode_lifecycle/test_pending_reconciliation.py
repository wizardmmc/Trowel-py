from __future__ import annotations

import pytest

from trowel_py.model_os.store import EpisodeCommandError, ModelOsStore
from trowel_py.model_os.types import (
    EpisodeStatus,
    ReconcileReason,
    TaskStatus,
    WaitingSubtype,
    WorkItemStatus,
)
from tests.model_os._episode_helpers import (
    FakeClock,
    activate_episode,
    make_pending,
    make_running_task_episode,
)


def test_n4_resolve_rejects_when_task_no_longer_waiting(
    store: ModelOsStore, monkeypatch
) -> None:
    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, task, _ = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    store.suspend_episode(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        pending=make_pending(cause="need input", native_generation="g1"),
    )
    store.cancel_task(task.task_id, reason="abandoned")
    with pytest.raises(EpisodeCommandError):
        store.resolve_episode_wait(episode.episode_id, answer_correlation_id="corr-1")
    assert (
        store.read_snapshot().episode_by_id(episode.episode_id).status
        == EpisodeStatus.SUSPENDED_WAITING_INPUT
    )


def test_n5_mark_lost_from_suspended_ready_unschedules_task(
    store: ModelOsStore, monkeypatch
) -> None:
    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, task, work_item_id = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    store.suspend_episode(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        pending=make_pending(cause="need input", native_generation="g1"),
    )
    store.resolve_episode_wait(episode.episode_id, answer_correlation_id="corr-1")
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
    assert task_state.waiting_condition.subtype == WaitingSubtype.REQUIRES_USER_RESTART
    wi = next(w for w in snap.work_items if w.work_item_id == work_item_id)
    assert wi.status == WorkItemStatus.SUSPENDED


def test_resolve_reconcile_close_restores_task_and_work_item(
    store: ModelOsStore, monkeypatch
) -> None:
    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, task, work_item_id = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    store.suspend_episode(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        pending=make_pending(cause="need input", native_generation="g1"),
    )
    store.mark_pending_channel_lost(
        episode.episode_id, reason=ReconcileReason.REQUIRES_USER_RESTART
    )

    store.resolve_reconcile(
        episode.episode_id, decision="close", confirmed_by="human-1"
    )
    snap = store.read_snapshot()
    assert snap.episode_by_id(episode.episode_id).status.value == "closed"
    task_state = next(t for t in snap.tasks if t.task_id == task.task_id)
    assert task_state.status == TaskStatus.READY
    work_item = next(w for w in snap.work_items if w.work_item_id == work_item_id)
    assert work_item.status == WorkItemStatus.READY


def test_mark_pending_channel_lost_reclaims_scheduler_task(
    store: ModelOsStore, monkeypatch
) -> None:
    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, task, _ = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    store.suspend_episode(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        pending=make_pending(cause="need input", native_generation="g1"),
    )
    store.resolve_episode_wait(episode.episode_id, answer_correlation_id="corr-1")
    # 模拟 Task 变为 READY 后被 scheduler 抢先领取的 TOCTOU 窗口。
    store.claim_foreground(task.task_id)
    assert store.read_foreground_task_id() == task.task_id

    store.mark_pending_channel_lost(
        episode.episode_id, reason=ReconcileReason.REQUIRES_USER_RESTART
    )
    snapshot = store.read_snapshot()
    assert (
        snapshot.episode_by_id(episode.episode_id).status
        == EpisodeStatus.RECONCILE_REQUIRED
    )
    task_state = next(t for t in snapshot.tasks if t.task_id == task.task_id)
    assert task_state.status == TaskStatus.WAITING_USER
    assert task_state.waiting_condition.subtype == WaitingSubtype.REQUIRES_USER_RESTART
    assert store.read_foreground_task_id() is None


def test_reconcile_required_blocks_normal_resume_paths(
    store: ModelOsStore, monkeypatch
) -> None:
    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _, _ = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    store.suspend_episode(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        pending=make_pending(cause="need input", native_generation="g1"),
    )
    store.mark_pending_channel_lost(
        episode.episode_id, reason=ReconcileReason.REQUIRES_USER_RESTART
    )
    reconciled = store.read_snapshot().episode_by_id(episode.episode_id)
    assert reconciled is not None
    assert reconciled.status == EpisodeStatus.RECONCILE_REQUIRED

    with pytest.raises(EpisodeCommandError):
        store.resolve_episode_wait(episode.episode_id, answer_correlation_id="corr-1")
    with pytest.raises(EpisodeCommandError):
        store.activate_suspended_episode(
            episode.episode_id,
            expected_lease_id=lease.lease_id,
            expected_owner=lease.owner,
            expected_token=lease.fencing_token,
        )
    unchanged = store.read_snapshot().episode_by_id(episode.episode_id)
    assert unchanged is not None
    assert unchanged.status == EpisodeStatus.RECONCILE_REQUIRED
