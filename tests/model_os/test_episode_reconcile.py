"""丢失 pending channel 后的 reconcile_required 进入、关闭与安全恢复测试。

该状态只能由显式 reconcile 决策退出；``resume_safe`` 先进入
``suspended_ready``，再复用正常激活路径取得 foreground。
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


def test_mark_lost_sets_task_waiting_subtype_to_requires_user_restart(
    store: ModelOsStore, monkeypatch
) -> None:
    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, task, _ = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    _park_in_reconcile(store, episode, lease, task, monkeypatch=monkeypatch)

    snap = store.read_snapshot()
    task_state = next(t for t in snap.tasks if t.task_id == task.task_id)
    assert task_state.status == TaskStatus.WAITING_USER
    assert task_state.waiting_condition is not None
    assert task_state.waiting_condition.subtype == WaitingSubtype.REQUIRES_USER_RESTART


def test_mark_lost_only_from_suspended_states(store: ModelOsStore, monkeypatch) -> None:
    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _, _ = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    with pytest.raises(EpisodeCommandError):
        store.mark_pending_channel_lost(
            episode.episode_id, reason=ReconcileReason.REQUIRES_USER_RESTART
        )


def test_resolve_close_always_produces_recovery_snapshot(
    store: ModelOsStore, monkeypatch
) -> None:
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
    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, task, _ = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    _park_in_reconcile(store, episode, lease, task, monkeypatch=monkeypatch)

    store.resolve_reconcile(
        episode.episode_id, decision="close", confirmed_by="human-1"
    )

    # 本路径没有更早的协作式 checkpoint，因此不应产生 checkpoint_committed。
    ck_events = [
        ev
        for _, ev in store.list_events()
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
    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, task, _ = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    _park_in_reconcile(store, episode, lease, task, monkeypatch=monkeypatch)

    store.resolve_reconcile(
        episode.episode_id, decision="close", confirmed_by="human-1"
    )
    resolved_events = [
        ev
        for _, ev in store.list_events()
        if ev.kind == EventKind.EPISODE_RECONCILE_RESOLVED
        and ev.episode_id == episode.episode_id
    ]
    assert len(resolved_events) == 1
    assert resolved_events[0].provenance == Provenance.USER_DECISION
    assert resolved_events[0].payload.get("confirmed_by") == "human-1"


def test_resolve_resume_safe_goes_via_suspended_ready(
    store: ModelOsStore, monkeypatch
) -> None:
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
    # resume_safe 不抢占 foreground。
    assert store.read_foreground_task_id() is None


def test_resume_safe_then_activate_reaches_active(
    store: ModelOsStore, monkeypatch
) -> None:
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
    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _, _ = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    # Episode 仍为 ACTIVE，resolve 必须拒绝。
    with pytest.raises(EpisodeCommandError):
        store.resolve_reconcile(
            episode.episode_id, decision="close", confirmed_by="human-1"
        )
