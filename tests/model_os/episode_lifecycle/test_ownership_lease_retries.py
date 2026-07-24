from __future__ import annotations

import pytest

from trowel_py.model_os.store import LeaseConflict, ModelOsStore
from tests.model_os._episode_helpers import FakeClock


def test_lease_idempotency_key_is_reusable_after_release(
    store: ModelOsStore, monkeypatch
) -> None:
    clock = FakeClock()
    clock.install(monkeypatch)
    first = store.acquire_lease(
        resource_type="episode_ownership",
        resource_id="ep-1",
        owner="runner-A",
        ttl_seconds=300,
        idempotency_key="K",
    )
    store.release_lease(first.lease_id)
    second = store.acquire_lease(
        resource_type="episode_ownership",
        resource_id="ep-1",
        owner="runner-A",
        ttl_seconds=300,
        idempotency_key="K",
    )
    assert second.lease_id != first.lease_id
    assert second.fencing_token > first.fencing_token


def test_idempotent_ownership_retry_refuses_expired_lease(
    store: ModelOsStore, monkeypatch
) -> None:
    clock = FakeClock()
    clock.install(monkeypatch)
    store.acquire_episode_ownership(
        "ep-1", owner="runner-A", ttl_seconds=60, idempotency_key="K"
    )
    clock.advance(61)
    with pytest.raises(LeaseConflict):
        store.acquire_episode_ownership(
            "ep-1", owner="runner-A", ttl_seconds=60, idempotency_key="K"
        )
