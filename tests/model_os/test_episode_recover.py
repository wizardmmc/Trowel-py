"""Episode ownership 接管与 RECOVERING 状态转换的原子性测试。

校验 Episode、接管过期 lease、提升 fencing token 和写入 RECOVERING 事件共享一个
IMMEDIATE 事务；任一步失败都不能留下孤立 lease。
"""

from __future__ import annotations

import pytest

from trowel_py.model_os.store import (
    EpisodeCommandError,
    LeaseConflict,
    ModelOsStore,
)
from trowel_py.model_os.types import EpisodeStatus

from tests.model_os._episode_helpers import (
    FakeClock,
    activate_episode,
    make_running_system_episode,
)


def _episode_with_expired_lease(store, monkeypatch, *, ttl=60):
    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _ = make_running_system_episode(store, ttl_seconds=ttl)
    activate_episode(store, episode.episode_id, lease)
    clock.advance(ttl + 1)  # 超过 TTL
    return episode, lease


def test_recover_rejects_unknown_episode(store: ModelOsStore) -> None:
    with pytest.raises(EpisodeCommandError):
        store.recover_episode(
            "does-not-exist",
            new_owner="runner-B",
            ttl_seconds=300,
            idempotency_key="recover-1",
            reason="restart",
        )
    row = store._conn.execute(
        "SELECT COUNT(*) AS n FROM leases WHERE resource_id='does-not-exist'"
    ).fetchone()
    assert int(row["n"]) == 0, "no orphan lease for an unknown episode"


def test_recover_rejects_terminal_episode(store: ModelOsStore, monkeypatch) -> None:
    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _ = make_running_system_episode(store)
    activate_episode(store, episode.episode_id, lease)
    # 通过合法路径关闭。
    store.request_yield(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        reason="done",
    )
    from tests.model_os._episode_helpers import make_cooperative_snapshot

    store.commit_checkpoint(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        snapshot=make_cooperative_snapshot(),
        checkpoint_key="ck-close-1",
    )
    store.close_episode(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
    )
    assert (
        store.read_snapshot().episode_by_id(episode.episode_id).status
        == EpisodeStatus.CLOSED
    )

    with pytest.raises(EpisodeCommandError):
        store.recover_episode(
            episode.episode_id,
            new_owner="runner-B",
            ttl_seconds=300,
            idempotency_key="recover-term",
            reason="restart",
        )


def test_recover_refuses_while_lease_still_live(
    store: ModelOsStore, monkeypatch
) -> None:
    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _ = make_running_system_episode(store, ttl_seconds=300)
    activate_episode(store, episode.episode_id, lease)
    # 不推进时钟，lease 仍有效。
    with pytest.raises(LeaseConflict):
        store.recover_episode(
            episode.episode_id,
            new_owner="runner-B",
            ttl_seconds=300,
            idempotency_key="recover-live",
            reason="restart",
        )


def test_recover_takes_over_expired_lease_atomically(
    store: ModelOsStore, monkeypatch
) -> None:
    episode, old_lease = _episode_with_expired_lease(store, monkeypatch, ttl=60)

    new_lease = store.recover_episode(
        episode.episode_id,
        new_owner="runner-B",
        ttl_seconds=300,
        idempotency_key="recover-ok",
        reason="restart",
    )
    assert new_lease.fencing_token > old_lease.fencing_token
    assert new_lease.owner == "runner-B"

    snap = store.read_snapshot()
    assert snap.episode_by_id(episode.episode_id).status == EpisodeStatus.RECOVERING

    # 旧授权作为已释放历史保留。
    old_row = store._conn.execute(
        "SELECT released_at FROM leases WHERE lease_id=?", (old_lease.lease_id,)
    ).fetchone()
    assert old_row["released_at"] is not None

    # 直接调用 ownership 原语验证旧 runner 已被 fencing；更高层 request_yield 会先
    # 因 RECOVERING 状态拒绝，无法覆盖 token 校验。
    from trowel_py.model_os.store import StaleWriterRejected

    with pytest.raises(StaleWriterRejected):
        store._check_ownership_in_tx(
            episode.episode_id,
            old_lease.lease_id,
            old_lease.owner,
            old_lease.fencing_token,
        )


def test_recover_is_idempotent_on_retry(store: ModelOsStore, monkeypatch) -> None:
    episode, _ = _episode_with_expired_lease(store, monkeypatch, ttl=60)
    first = store.recover_episode(
        episode.episode_id,
        new_owner="runner-B",
        ttl_seconds=300,
        idempotency_key="recover-idem",
        reason="restart",
    )
    second = store.recover_episode(
        episode.episode_id,
        new_owner="runner-B",
        ttl_seconds=300,
        idempotency_key="recover-idem",
        reason="restart",
    )
    assert first.lease_id == second.lease_id
    assert first.fencing_token == second.fencing_token


def test_recover_then_checkpoint_recovery_partial(
    store: ModelOsStore, monkeypatch
) -> None:

    episode, _ = _episode_with_expired_lease(store, monkeypatch, ttl=60)
    new_lease = store.recover_episode(
        episode.episode_id,
        new_owner="runner-B",
        ttl_seconds=300,
        idempotency_key="recover-ck",
        reason="restart",
    )
    ref = store.checkpoint_recovery_partial(
        episode.episode_id,
        expected_lease_id=new_lease.lease_id,
        expected_owner=new_lease.owner,
        expected_token=new_lease.fencing_token,
        reason="post-crash recovery",
        checkpoint_key="recover-ck-1",
    )
    assert ref.episode_id == episode.episode_id
    assert ref.version >= 1
