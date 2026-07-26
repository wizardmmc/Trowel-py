from __future__ import annotations

import pytest

from trowel_py.model_os.store import EpisodeCommandError, LeaseConflict, ModelOsStore
from trowel_py.model_os.types import EpisodeStatus
from tests.model_os._episode_helpers import (
    FakeClock,
    activate_episode,
    make_cooperative_snapshot,
    make_running_system_episode,
    make_running_task_episode,
    make_pending,
)


def test_n2_commit_checkpoint_same_key_retry_returns_original_ref(
    store: ModelOsStore, monkeypatch
) -> None:
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
    # 同 key 重试必须先于状态门禁解析，CHECKPOINTING 仍应返回原引用。
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


def _episode_recovering(store, monkeypatch, *, ttl=60):
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
    episode, new_lease = _episode_recovering(store, monkeypatch)
    with pytest.raises(EpisodeCommandError):
        store.close_recovered_episode(
            episode.episode_id,
            expected_lease_id=new_lease.lease_id,
            expected_owner=new_lease.owner,
            expected_token=new_lease.fencing_token,
        )


def test_start_episode_retry_after_ownership_takeover_raises(
    store: ModelOsStore, monkeypatch
) -> None:
    clock = FakeClock()
    clock.install(monkeypatch)
    episode, old_lease, _ = make_running_system_episode(
        store, ttl_seconds=60, idempotency_key="ep-K"
    )
    activate_episode(store, episode.episode_id, old_lease)
    clock.advance(61)
    store.recover_episode(
        episode.episode_id,
        new_owner="runner-B",
        ttl_seconds=300,
        idempotency_key="recover-K",
        reason="restart",
    )
    # 原 owner 使用相同 key 重试时，不能拿到 takeover 后的新 owner lease。
    with pytest.raises(LeaseConflict):
        store.start_episode(
            work_item_id=episode.work_item_id,
            owner="runner-A",
            ttl_seconds=300,
            idempotency_key="ep-K",
            task_id=None,
        )


def test_recovery_partial_refuses_pending_suspended_episode(
    store: ModelOsStore, monkeypatch
) -> None:
    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _ = make_running_system_episode(store)
    activate_episode(store, episode.episode_id, lease)
    store.suspend_episode(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        pending=make_pending(),
    )
    with pytest.raises(EpisodeCommandError, match="suspended_waiting_input"):
        store.checkpoint_recovery_partial(
            episode.episode_id,
            expected_lease_id=lease.lease_id,
            expected_owner=lease.owner,
            expected_token=lease.fencing_token,
            reason="must keep pending binding",
            checkpoint_key="pending-must-not-checkpoint",
        )


def test_forced_recovery_partial_retry_resolves_before_checkpointing_gate(
    store: ModelOsStore, monkeypatch
) -> None:
    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _ = make_running_system_episode(store)
    activate_episode(store, episode.episode_id, lease)
    store.request_yield(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        reason="hard_budget",
    )
    first = store.checkpoint_recovery_partial(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        reason="hard_budget",
        checkpoint_key="forced-retry",
    )
    assert store.read_snapshot().episode_by_id(episode.episode_id).status == EpisodeStatus.CHECKPOINTING
    second = store.checkpoint_recovery_partial(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        reason="hard_budget",
        checkpoint_key="forced-retry",
    )
    assert second == first


def test_interrupt_reconcile_rejects_parent_state_drift_before_writing(
    store: ModelOsStore, monkeypatch
) -> None:
    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _, _ = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    store.request_yield(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        reason="runtime_timeout",
    )
    store.checkpoint_recovery_partial(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        reason="runtime_timeout",
        checkpoint_key="drift-recovery",
    )
    store.release_foreground()

    with pytest.raises(EpisodeCommandError, match="WorkItem .* RUNNING"):
        store.require_reconcile_after_interrupt(
            episode.episode_id,
            expected_lease_id=lease.lease_id,
            expected_owner=lease.owner,
            expected_token=lease.fencing_token,
            reason="unknown_requires_reconcile",
        )

    current = store.read_snapshot().episode_by_id(episode.episode_id)
    assert current.status == EpisodeStatus.CHECKPOINTING
    row = store._read_episode_lease_row(episode.episode_id)
    assert row is not None and row["released_at"] is None


def test_closed_episode_settlement_never_releases_another_foreground_task(
    store: ModelOsStore, monkeypatch
) -> None:
    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, first, _ = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    store.request_yield(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        reason="done",
    )
    store.commit_checkpoint(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        snapshot=make_cooperative_snapshot(),
        checkpoint_key="settlement-drift",
    )
    store.close_episode(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
    )
    second = store.create_task_from_user_request(
        original_goal="other task", idempotency_key="other-task"
    )
    store.promote_to_warm(second.task_id)
    with store._tx():
        store._conn.execute(
            "UPDATE foreground_claim SET task_id=? WHERE id=1",
            (second.task_id,),
        )

    with pytest.raises(EpisodeCommandError, match="no longer owns foreground"):
        store.settle_closed_episode(episode.episode_id)

    snapshot = store.read_snapshot()
    assert snapshot.foreground_task_id == second.task_id
    assert next(item for item in snapshot.tasks if item.task_id == first.task_id).status.value == "running"
