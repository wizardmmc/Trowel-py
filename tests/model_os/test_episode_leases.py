"""Episode ownership lease 的资源域幂等、接管历史与 fencing 高水位测试。"""

from __future__ import annotations

import pytest

from trowel_py.model_os.store import LeaseConflict, ModelOsStore

from tests.model_os._episode_helpers import FakeClock


def test_same_idempotency_key_across_different_resource_types_does_not_clash(
    store: ModelOsStore, monkeypatch
) -> None:
    clock = FakeClock()
    clock.install(monkeypatch)

    ep_lease = store.acquire_lease(
        resource_type="episode_ownership",
        resource_id="ep-1",
        owner="runner-A",
        ttl_seconds=60,
        idempotency_key="retry-key-1",
    )
    wl_lease = store.acquire_lease(
        resource_type="work_lease",
        resource_id="wl-1",
        owner="runner-A",
        ttl_seconds=60,
        idempotency_key="retry-key-1",  # 相同键用于不同资源
    )
    assert ep_lease.resource_type == "episode_ownership"
    assert ep_lease.resource_id == "ep-1"
    assert wl_lease.resource_type == "work_lease"
    assert wl_lease.resource_id == "wl-1"
    assert ep_lease.lease_id != wl_lease.lease_id


def test_same_key_same_resource_returns_original_lease(store: ModelOsStore) -> None:
    first = store.acquire_lease(
        resource_type="episode_ownership",
        resource_id="ep-1",
        owner="runner-A",
        ttl_seconds=60,
        idempotency_key="retry-key-1",
    )
    second = store.acquire_lease(
        resource_type="episode_ownership",
        resource_id="ep-1",
        owner="runner-A",
        ttl_seconds=60,
        idempotency_key="retry-key-1",
    )
    assert first.lease_id == second.lease_id
    assert first.fencing_token == second.fencing_token


def test_same_key_same_resource_different_owner_is_conflict(
    store: ModelOsStore,
) -> None:
    store.acquire_lease(
        resource_type="episode_ownership",
        resource_id="ep-1",
        owner="runner-A",
        ttl_seconds=60,
        idempotency_key="retry-key-1",
    )
    with pytest.raises(LeaseConflict):
        store.acquire_lease(
            resource_type="episode_ownership",
            resource_id="ep-1",
            owner="runner-B",  # 不同 owner
            ttl_seconds=60,
            idempotency_key="retry-key-1",
        )


def test_same_key_different_resource_id_same_type_is_separate(
    store: ModelOsStore,
) -> None:
    a = store.acquire_lease(
        resource_type="episode_ownership",
        resource_id="ep-A",
        owner="runner-A",
        ttl_seconds=60,
        idempotency_key="retry-key-1",
    )
    b = store.acquire_lease(
        resource_type="episode_ownership",
        resource_id="ep-B",
        owner="runner-A",
        ttl_seconds=60,
        idempotency_key="retry-key-1",
    )
    assert a.resource_id == "ep-A"
    assert b.resource_id == "ep-B"
    assert a.lease_id != b.lease_id


def test_expired_takeover_marks_old_released_and_inserts_new_row(
    store: ModelOsStore, monkeypatch
) -> None:
    clock = FakeClock()
    clock.install(monkeypatch)

    old = store.acquire_lease(
        resource_type="episode_ownership",
        resource_id="ep-1",
        owner="runner-A",
        ttl_seconds=60,
        idempotency_key="old-key",
    )
    clock.advance(61)  # 超过 TTL

    new = store.acquire_lease(
        resource_type="episode_ownership",
        resource_id="ep-1",
        owner="runner-B",
        ttl_seconds=60,
        idempotency_key="new-key",
    )

    # 旧授权仍保留，但已释放。
    row = store._conn.execute(
        "SELECT * FROM leases WHERE lease_id=?", (old.lease_id,)
    ).fetchone()
    assert row is not None, "expired grant must remain in the table (history)"
    assert row["released_at"] is not None, "expired grant must be marked released"
    assert row["owner"] == "runner-A"

    # 新授权使用独立的活跃行。
    assert new.lease_id != old.lease_id
    assert new.owner == "runner-B"
    new_row = store._conn.execute(
        "SELECT released_at FROM leases WHERE lease_id=?", (new.lease_id,)
    ).fetchone()
    assert new_row["released_at"] is None


def test_takeover_mints_strictly_higher_fencing_token(
    store: ModelOsStore, monkeypatch
) -> None:
    clock = FakeClock()
    clock.install(monkeypatch)

    first = store.acquire_lease(
        resource_type="episode_ownership",
        resource_id="ep-1",
        owner="runner-A",
        ttl_seconds=60,
    )
    clock.advance(61)
    second = store.acquire_lease(
        resource_type="episode_ownership",
        resource_id="ep-1",
        owner="runner-B",
        ttl_seconds=60,
    )
    assert second.fencing_token > first.fencing_token


def test_fence_counter_does_not_regress_when_old_lease_rows_gc(
    store: ModelOsStore, monkeypatch
) -> None:
    clock = FakeClock()
    clock.install(monkeypatch)

    store.acquire_lease(
        resource_type="episode_ownership",
        resource_id="ep-1",
        owner="runner-A",
        ttl_seconds=60,
    )
    clock.advance(61)
    second = store.acquire_lease(
        resource_type="episode_ownership",
        resource_id="ep-1",
        owner="runner-B",
        ttl_seconds=60,
    )
    token_after_two = second.fencing_token
    assert token_after_two >= 2

    # 模拟清理：物理删除已释放的旧 lease 行。
    store._conn.execute("DELETE FROM leases WHERE released_at IS NOT NULL")

    # 独立计数器仍须保留 token 高水位。
    counter_row = store._conn.execute(
        "SELECT last_token FROM lease_fence_counters "
        "WHERE resource_type='episode_ownership' AND resource_id='ep-1'"
    ).fetchone()
    assert int(counter_row["last_token"]) == token_after_two, (
        "GC of old lease rows must not change the fence counter"
    )

    # 强制活跃 lease 到期后再次授权，token 仍须递增。
    clock.advance(61)
    third = store.acquire_lease(
        resource_type="episode_ownership",
        resource_id="ep-1",
        owner="runner-C",
        ttl_seconds=60,
    )
    assert third.fencing_token > token_after_two, (
        "a grant after GC must still mint a higher token, not restart from 1"
    )


def test_fence_counter_is_per_resource(store: ModelOsStore, monkeypatch) -> None:
    clock = FakeClock()
    clock.install(monkeypatch)

    ep1 = store.acquire_lease(
        resource_type="episode_ownership",
        resource_id="ep-1",
        owner="runner-A",
        ttl_seconds=60,
    )
    ep2 = store.acquire_lease(
        resource_type="episode_ownership",
        resource_id="ep-2",
        owner="runner-A",
        ttl_seconds=60,
    )
    assert ep1.fencing_token == 1
    assert ep2.fencing_token == 1  # 每个资源使用独立计数器
