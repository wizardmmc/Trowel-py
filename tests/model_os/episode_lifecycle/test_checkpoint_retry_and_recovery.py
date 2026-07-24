from __future__ import annotations

import pytest

from trowel_py.model_os.store import EpisodeCommandError, LeaseConflict, ModelOsStore
from trowel_py.model_os.types import EpisodeStatus
from tests.model_os._episode_helpers import (
    FakeClock,
    activate_episode,
    make_cooperative_snapshot,
    make_running_system_episode,
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
