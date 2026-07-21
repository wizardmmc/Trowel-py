"""Concurrency / CAS lease tests (slice-084 pass criterion 2).

Two concurrent claims on the same resource must result in exactly one lease
owner. The store enforces this at the SQLite level via a partial unique index
on active leases, so the test uses two separate connections (real WAL write
serialization) rather than mocking.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from trowel_py.model_os.store import LeaseConflict, ModelOsStore


def test_concurrent_claims_only_one_winner(db_path) -> None:  # noqa: ANN001
    """Two threads race to claim the same resource; only one wins.

    Each racer opens its own connection to the same WAL file, so write
    serialization happens at the SQLite file-lock level — the real
    production concurrency path.
    """

    # bootstrap the schema once before the race so racers only contend on
    # the INSERT, not on CREATE TABLE.
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
    """A second claim on an already-held resource raises LeaseConflict."""

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
    """After the holder releases, the resource is claimable again."""

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
    """A lease past its expiry can be claimed by a new owner (TTL enforced)."""

    store.acquire_lease(
        resource_type="work_item",
        resource_id="wi-exp",
        owner="alice",
        ttl_seconds=0,  # expires immediately
    )
    # busy-wait is blocked in this harness; sleep via time.time loop is fine
    # for a sub-second TTL — give the expiry a moment to elapse.
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
    """Re-claiming with the same idempotency key returns the original lease."""

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
    """The same idempotency key under a different owner is a conflict — the
    key is the original owner's retry identity, not a transferable handle,
    so a stranger must not reclaim or release another owner's lease."""

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
            owner="bob",  # different owner, same key
            ttl_seconds=60,
            idempotency_key="op-456",
        )
