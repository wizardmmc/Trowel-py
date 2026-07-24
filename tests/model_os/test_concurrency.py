"""lease CAS 并发测试；用两个真实连接覆盖 SQLite WAL 文件锁边界。"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from trowel_py.model_os.store import LeaseConflict, ModelOsStore


def test_concurrent_claims_only_one_winner(db_path) -> None:  # noqa: ANN001
    # 竞争前先初始化 schema，使线程只在 INSERT 上竞争，不混入 DDL 文件锁。
    bootstrapper = ModelOsStore(db_path)
    bootstrapper.open()
    bootstrapper.close()

    winners: list[str] = []
    losers: list[Exception] = []
    lock = threading.Lock()

    def racer(owner: str) -> None:
        local = ModelOsStore(db_path)
        local.open()
        try:
            lease = local.acquire_lease(
                resource_type="work_item",
                resource_id="wi-race",
                owner=owner,
                ttl_seconds=60,
            )
            with lock:
                winners.append(lease.owner)
        except LeaseConflict as exc:
            with lock:
                losers.append(exc)
        finally:
            local.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(racer, owner) for owner in ("alice", "bob")]
        for f in futures:
            f.result()

    assert len(winners) == 1, f"expected exactly one winner, got {winners}"
    assert len(losers) == 1


def test_second_acquire_on_held_resource_conflicts(store: ModelOsStore) -> None:
    store.acquire_lease(
        resource_type="work_item",
        resource_id="wi-held",
        owner="alice",
        ttl_seconds=60,
    )
    with pytest.raises(LeaseConflict):
        store.acquire_lease(
            resource_type="work_item",
            resource_id="wi-held",
            owner="bob",
            ttl_seconds=60,
        )


def test_release_allows_next_owner(store: ModelOsStore) -> None:
    lease = store.acquire_lease(
        resource_type="work_item",
        resource_id="wi-rel",
        owner="alice",
        ttl_seconds=60,
    )
    assert store.release_lease(lease.lease_id) is True

    second = store.acquire_lease(
        resource_type="work_item",
        resource_id="wi-rel",
        owner="bob",
        ttl_seconds=60,
    )
    assert second.owner == "bob"


def test_expired_lease_can_be_taken_over(store: ModelOsStore) -> None:
    store.acquire_lease(
        resource_type="work_item",
        resource_id="wi-exp",
        owner="alice",
        ttl_seconds=0,  # 立即到期
    )
    # 测试环境禁止忙等；短暂轮询等待到期边界生效。
    deadline = time.time() + 2
    while time.time() < deadline:
        try:
            takeover = store.acquire_lease(
                resource_type="work_item",
                resource_id="wi-exp",
                owner="bob",
                ttl_seconds=60,
            )
            assert takeover.owner == "bob"
            return
        except LeaseConflict:
            time.sleep(0.05)
    pytest.fail("expired lease was never released for takeover")


def test_idempotency_key_reclaims_same_lease(store: ModelOsStore) -> None:
    first = store.acquire_lease(
        resource_type="work_item",
        resource_id="wi-idem",
        owner="alice",
        ttl_seconds=60,
        idempotency_key="op-123",
    )
    second = store.acquire_lease(
        resource_type="work_item",
        resource_id="wi-idem",
        owner="alice",
        ttl_seconds=60,
        idempotency_key="op-123",
    )
    assert second.lease_id == first.lease_id


def test_idempotency_key_with_different_owner_conflicts(
    store: ModelOsStore,
) -> None:
    store.acquire_lease(
        resource_type="work_item",
        resource_id="wi-xfer",
        owner="alice",
        ttl_seconds=60,
        idempotency_key="op-456",
    )
    with pytest.raises(LeaseConflict):
        store.acquire_lease(
            resource_type="work_item",
            resource_id="wi-xfer",
            owner="bob",  # 不同 owner 复用同一键
            ttl_seconds=60,
            idempotency_key="op-456",
        )
