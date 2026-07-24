"""Episode 审计持久性、snapshot 写入校验与 checkpoint key 隔离测试。"""

from __future__ import annotations

import pytest

from trowel_py.model_os.store import EpisodeCommandError, ModelOsStore
from trowel_py.model_os.types import (
    EventKind,
    SideEffectRecord,
)

from tests.model_os._episode_helpers import (
    FakeClock,
    activate_episode,
    make_cooperative_snapshot,
    make_running_system_episode,
)


def test_stale_write_leaves_durable_audit(store: ModelOsStore, monkeypatch) -> None:
    from trowel_py.model_os.store import StaleWriterRejected

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, old_lease, _ = make_running_system_episode(store, ttl_seconds=60)
    activate_episode(store, episode.episode_id, old_lease)
    clock.advance(61)
    store.acquire_episode_ownership(  # 由新 owner 接管
        episode.episode_id, owner="runner-B", ttl_seconds=60
    )
    with pytest.raises(StaleWriterRejected):
        store.request_yield(  # 旧 owner 恢复执行
            episode.episode_id,
            expected_lease_id=old_lease.lease_id,
            expected_owner=old_lease.owner,
            expected_token=old_lease.fencing_token,
            reason="stale",
        )

    audits = [
        ev
        for _, ev in store.list_events()
        if ev.kind == EventKind.LATE_WRITE_REJECTED
        and ev.episode_id == episode.episode_id
    ]
    assert len(audits) == 1, "late_write_rejected audit must survive the rollback"
    audit = audits[0]
    assert audit.payload["attempted_token"] == old_lease.fencing_token
    assert audit.payload["reason"]


def _active_system_episode(store, monkeypatch, *, ttl=300):
    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _ = make_running_system_episode(store, ttl_seconds=ttl)
    activate_episode(store, episode.episode_id, lease)
    store.request_yield(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        reason="done",
    )
    return episode, lease


def test_oversized_snapshot_payload_is_rejected(
    store: ModelOsStore, monkeypatch
) -> None:
    episode, lease = _active_system_episode(store, monkeypatch)
    big = make_cooperative_snapshot(current_judgment="x" * (300 * 1024))
    with pytest.raises(EpisodeCommandError):
        store.commit_checkpoint(
            episode.episode_id,
            expected_lease_id=lease.lease_id,
            expected_owner=lease.owner,
            expected_token=lease.fencing_token,
            snapshot=big,
            checkpoint_key="ck-big",
        )


def test_too_many_next_steps_is_rejected(store: ModelOsStore, monkeypatch) -> None:
    episode, lease = _active_system_episode(store, monkeypatch)
    bad = make_cooperative_snapshot(
        next_steps=("a", "b", "c", "d")  # 4 > 3
    )
    with pytest.raises(EpisodeCommandError):
        store.commit_checkpoint(
            episode.episode_id,
            expected_lease_id=lease.lease_id,
            expected_owner=lease.owner,
            expected_token=lease.fencing_token,
            snapshot=bad,
            checkpoint_key="ck-steps",
        )


def test_done_side_effect_without_evidence_is_rejected(
    store: ModelOsStore, monkeypatch
) -> None:
    episode, lease = _active_system_episode(store, monkeypatch)
    bad = make_cooperative_snapshot(
        side_effects=(
            SideEffectRecord(
                action_ref="action/x",
                idempotency_key="k",
                outcome="done",
                evidence_ref=None,  # 缺少证据
            ),
        )
    )
    with pytest.raises(EpisodeCommandError):
        store.commit_checkpoint(
            episode.episode_id,
            expected_lease_id=lease.lease_id,
            expected_owner=lease.owner,
            expected_token=lease.fencing_token,
            snapshot=bad,
            checkpoint_key="ck-noev",
        )


def test_checkpoint_key_conflict_across_episodes(
    store: ModelOsStore, monkeypatch
) -> None:
    clock = FakeClock()
    clock.install(monkeypatch)
    ep1, lease1, _ = make_running_system_episode(
        store, ttl_seconds=300, idempotency_key="ep-key-A"
    )
    activate_episode(store, ep1.episode_id, lease1)
    store.request_yield(
        ep1.episode_id,
        expected_lease_id=lease1.lease_id,
        expected_owner=lease1.owner,
        expected_token=lease1.fencing_token,
        reason="done",
    )
    ref1 = store.commit_checkpoint(
        ep1.episode_id,
        expected_lease_id=lease1.lease_id,
        expected_owner=lease1.owner,
        expected_token=lease1.fencing_token,
        snapshot=make_cooperative_snapshot(),
        checkpoint_key="shared-key",
    )
    assert ref1.episode_id == ep1.episode_id

    # 另一个 Episode 复用同一键。
    ep2, lease2, _ = make_running_system_episode(
        store, ttl_seconds=300, idempotency_key="ep-key-B"
    )
    activate_episode(store, ep2.episode_id, lease2)
    store.request_yield(
        ep2.episode_id,
        expected_lease_id=lease2.lease_id,
        expected_owner=lease2.owner,
        expected_token=lease2.fencing_token,
        reason="done",
    )
    with pytest.raises(EpisodeCommandError):
        store.commit_checkpoint(
            ep2.episode_id,
            expected_lease_id=lease2.lease_id,
            expected_owner=lease2.owner,
            expected_token=lease2.fencing_token,
            snapshot=make_cooperative_snapshot(),
            checkpoint_key="shared-key",  # 重复使用同一键
        )


def test_read_snapshot_fails_closed_when_committed_event_missing(
    store: ModelOsStore, monkeypatch
) -> None:
    episode, lease = _active_system_episode(store, monkeypatch)
    ref = store.commit_checkpoint(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        snapshot=make_cooperative_snapshot(),
        checkpoint_key="ck-clean",
    )
    # 篡改数据库：删除提交事件，只留下孤立快照行。
    store._conn.execute(
        "DELETE FROM events WHERE event_id=?", (ref.committed_event_id,)
    )
    with pytest.raises(EpisodeCommandError):
        store.read_episode_snapshot(ref)
