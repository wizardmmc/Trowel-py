"""Lease 共表修复 + fencing counter tests (slice-087 pass criteria 14, 15).

slice-087 reuses the 084 ``leases`` table for Episode ownership, and codex's
grill caught two pre-existing bugs in that table:

1. ``idx_leases_idem`` was globally unique on ``idempotency_key`` alone, so the
   same key could not be reused across an ``episode_ownership`` lease and a
   ``work_lease`` — one would silently return the OTHER resource's lease.
   Fix (spec line 200, pass 14): scope the index to
   ``(resource_type, resource_id, idempotency_key)`` and have ``acquire_lease``
   verify the resource when a key hit fires.

2. ``fencing_token`` used to be ``MAX(token)+1`` over the live rows, so garbage-
   collecting old lease rows would roll the token BACK. Fix (spec line 228,
   pass 15): a separate ``lease_fence_counters`` table holds the monotonic
   counter; GC of lease rows cannot touch it.

These tests assert both fixes hold. ``FakeClock`` drives TTL expiry so no real
wall-clock sleep is needed.
"""

from __future__ import annotations

import pytest

from trowel_py.model_os.store import LeaseConflict, ModelOsStore

from tests.model_os._episode_helpers import FakeClock


# ---------------------------------------------- idempotency key resource scope ---


def test_same_idempotency_key_across_different_resource_types_does_not_clash(
    store: ModelOsStore, monkeypatch
) -> None:
    """pass 14: idempotency_key is scoped to the RESOURCE. The same key on an
    episode_ownership lease and a work_lease must BOTH succeed and return the
    correct resource each — the v1 global-unique index returned the wrong
    resource's lease here."""

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
        idempotency_key="retry-key-1",  # SAME key, DIFFERENT resource
    )
    assert ep_lease.resource_type == "episode_ownership"
    assert ep_lease.resource_id == "ep-1"
    assert wl_lease.resource_type == "work_lease"
    assert wl_lease.resource_id == "wl-1"
    assert ep_lease.lease_id != wl_lease.lease_id


def test_same_key_same_resource_returns_original_lease(store: ModelOsStore) -> None:
    """Idempotent retry: same (resource, key, owner) returns the first lease,
    not a second row."""

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


def test_same_key_same_resource_different_owner_is_conflict(store: ModelOsStore) -> None:
    """A second owner trying the same (resource, key) is a conflict, NOT a
    silent transfer — the key is the first owner's retry identity."""

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
            owner="runner-B",  # different owner
            ttl_seconds=60,
            idempotency_key="retry-key-1",
        )


def test_same_key_different_resource_id_same_type_is_separate(store: ModelOsStore) -> None:
    """Two distinct episodes, same key, must each get their own lease — a key
    reused across two episode_ownership resources does not alias."""

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


# ---------------------------------------------- takeover keeps history ---


def test_expired_takeover_marks_old_released_and_inserts_new_row(
    store: ModelOsStore, monkeypatch
) -> None:
    """pass 14: takeover preserves history. The expired grant is marked
    released (not UPDATEd in place, not deleted), and a fresh row is inserted
    for the new owner. The v1 in-place UPDATE lost the old grant's audit trail
    and its idempotency key."""

    clock = FakeClock()
    clock.install(monkeypatch)

    old = store.acquire_lease(
        resource_type="episode_ownership",
        resource_id="ep-1",
        owner="runner-A",
        ttl_seconds=60,
        idempotency_key="old-key",
    )
    clock.advance(61)  # past TTL

    new = store.acquire_lease(
        resource_type="episode_ownership",
        resource_id="ep-1",
        owner="runner-B",
        ttl_seconds=60,
        idempotency_key="new-key",
    )

    # old grant still exists, now released
    row = store._conn.execute(
        "SELECT * FROM leases WHERE lease_id=?", (old.lease_id,)
    ).fetchone()
    assert row is not None, "expired grant must remain in the table (history)"
    assert row["released_at"] is not None, "expired grant must be marked released"
    assert row["owner"] == "runner-A"

    # new grant is a separate, active row
    assert new.lease_id != old.lease_id
    assert new.owner == "runner-B"
    new_row = store._conn.execute(
        "SELECT released_at FROM leases WHERE lease_id=?", (new.lease_id,)
    ).fetchone()
    assert new_row["released_at"] is None


def test_takeover_mints_strictly_higher_fencing_token(
    store: ModelOsStore, monkeypatch
) -> None:
    """Every new grant on a resource gets a strictly higher token than the
    previous grant — the fencing invariant (Kleppmann). The old holder's token
    must not pass a fenced write after takeover."""

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


# ---------------------------------------------- fence counter does not regress ---


def test_fence_counter_does_not_regress_when_old_lease_rows_gc(
    store: ModelOsStore, monkeypatch
) -> None:
    """pass 15: ``lease_fence_counters`` is a separate table. Deleting old
    lease rows (GC) must NOT roll the token back — the next grant still gets a
    higher token than every prior grant on that resource."""

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

    # Simulate GC: physically delete the released (old) lease row.
    store._conn.execute("DELETE FROM leases WHERE released_at IS NOT NULL")

    # the counter table must still remember the high-watermark
    counter_row = store._conn.execute(
        "SELECT last_token FROM lease_fence_counters "
        "WHERE resource_type='episode_ownership' AND resource_id='ep-1'"
    ).fetchone()
    assert int(counter_row["last_token"]) == token_after_two, (
        "GC of old lease rows must not change the fence counter"
    )

    # force the active lease to expire and grant again — token keeps climbing
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


def test_fence_counter_is_per_resource(
    store: ModelOsStore, monkeypatch
) -> None:
    """The counter is scoped to (resource_type, resource_id). Two different
    resources each start at token 1; they do not share a global counter."""

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
    assert ep2.fencing_token == 1  # independent counter per resource_id
