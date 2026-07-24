from __future__ import annotations

import pytest

from trowel_py.model_os.store import (
    EpisodeCommandError,
    ModelOsStore,
    StaleWriterRejected,
    _EPISODE_FENCED_KINDS,
)
from trowel_py.model_os.types import (
    Episode,
    EpisodeStatus,
    EventEnvelope,
    EventKind,
    Lease,
    Provenance,
    WorkItemStatus,
)

from tests.model_os._episode_helpers import (
    FakeClock,
    activate_episode,
    make_running_system_episode,
)


def test_externally_driven_episode_kinds_are_not_fenced() -> None:
    # 外部答案、宿主重启或人工决定到达时，调用者可能已经没有有效 lease。
    assert EventKind.EPISODE_WAIT_RESOLVED not in _EPISODE_FENCED_KINDS
    assert EventKind.EPISODE_RECONCILE_REQUIRED not in _EPISODE_FENCED_KINDS
    assert EventKind.EPISODE_RECONCILE_RESOLVED not in _EPISODE_FENCED_KINDS


def test_externally_driven_kinds_still_gated_against_bare_append(
    store: ModelOsStore,
) -> None:
    for kind in (
        EventKind.EPISODE_WAIT_RESOLVED,
        EventKind.EPISODE_RECONCILE_REQUIRED,
        EventKind.EPISODE_RECONCILE_RESOLVED,
    ):
        bad = EventEnvelope(
            event_id=f"forge.{kind}.{kind}",
            kind=kind,
            occurred_at="2026-07-21T00:00:00Z",
            source="attacker",
            provenance=Provenance.MACHINE_OBSERVATION,
            policy_version="v0",
            payload={"new_status": "active"},
            episode_id="ep-x",
        )
        with pytest.raises(EpisodeCommandError):
            store.append_event(bad)


def test_fenced_event_cannot_bypass_gates_by_omitting_triple(
    store: ModelOsStore,
) -> None:
    assert EventKind.EPISODE_YIELD_REQUESTED in _EPISODE_FENCED_KINDS
    episode, _, _ = make_running_system_episode(store)
    event = store._make_episode_event(
        EventKind.EPISODE_YIELD_REQUESTED,
        episode.episode_id,
        {"new_status": "yield_requested", "reason": "x"},
    )
    with pytest.raises(EpisodeCommandError):
        store.append_event(event)
    with store._tx():
        with pytest.raises(EpisodeCommandError, match="must carry"):
            store._append_fenced_event_in_tx(event)


def _active_system_episode(
    store: ModelOsStore,
    monkeypatch: pytest.MonkeyPatch,
    *,
    ttl: int = 300,
) -> tuple[Episode, Lease]:
    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _ = make_running_system_episode(store, ttl_seconds=ttl)
    activate_episode(store, episode.episode_id, lease)
    return episode, lease


@pytest.mark.parametrize("mismatch", ["lease_id", "owner", "token"])
def test_mismatched_ownership_triple_is_rejected(
    store: ModelOsStore,
    monkeypatch: pytest.MonkeyPatch,
    mismatch: str,
) -> None:
    episode, lease = _active_system_episode(store, monkeypatch)
    with pytest.raises(StaleWriterRejected):
        store.request_yield(
            episode.episode_id,
            expected_lease_id=(
                "wrong-lease-id" if mismatch == "lease_id" else lease.lease_id
            ),
            expected_owner="impostor" if mismatch == "owner" else lease.owner,
            expected_token=(
                lease.fencing_token + 999
                if mismatch == "token"
                else lease.fencing_token
            ),
            reason="r",
        )
    state = store.read_snapshot().episode_by_id(episode.episode_id)
    assert state is not None
    assert state.status == EpisodeStatus.ACTIVE


def test_expired_lease_without_takeover_is_rejected(
    store: ModelOsStore, monkeypatch
) -> None:
    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _ = make_running_system_episode(store, ttl_seconds=60)
    activate_episode(store, episode.episode_id, lease)
    clock.advance(61)

    with pytest.raises(StaleWriterRejected):
        store.request_yield(
            episode.episode_id,
            expected_lease_id=lease.lease_id,
            expected_owner=lease.owner,
            expected_token=lease.fencing_token,
            reason="r",
        )


def test_stale_owner_after_takeover_is_rejected_but_new_owner_can_write(
    store: ModelOsStore, monkeypatch
) -> None:
    clock = FakeClock()
    clock.install(monkeypatch)
    episode, old_lease, _ = make_running_system_episode(store, ttl_seconds=60)
    activate_episode(store, episode.episode_id, old_lease)

    clock.advance(61)
    new_lease = store.acquire_episode_ownership(
        episode.episode_id, owner="runner-B", ttl_seconds=60
    )
    assert new_lease.fencing_token > old_lease.fencing_token

    with pytest.raises(StaleWriterRejected):
        store.request_yield(
            episode.episode_id,
            expected_lease_id=old_lease.lease_id,
            expected_owner=old_lease.owner,
            expected_token=old_lease.fencing_token,
            reason="stale",
        )

    store.request_yield(
        episode.episode_id,
        expected_lease_id=new_lease.lease_id,
        expected_owner=new_lease.owner,
        expected_token=new_lease.fencing_token,
        reason="fresh",
    )
    state = store.read_snapshot().episode_by_id(episode.episode_id)
    assert state is not None
    assert state.status == EpisodeStatus.YIELD_REQUESTED


def test_work_item_event_carrying_episode_id_is_not_fencing_checked(
    store: ModelOsStore,
) -> None:
    episode, _, work_item_id = make_running_system_episode(store)
    event = EventEnvelope(
        event_id="work-item.with-ep-ref",
        kind=EventKind.WORK_ITEM_STATUS_CHANGED,
        occurred_at="2026-07-21T00:00:00Z",
        source="test",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v0",
        payload={"new_status": WorkItemStatus.SUSPENDED.value},
        work_item_id=work_item_id,
        # 非 Episode 事件可用 episode_id 表示因果关联，不因此进入 fencing。
        episode_id=episode.episode_id,
    )
    store.append_event(event)
    assert any(
        persisted.event_id == event.event_id for _, persisted in store.list_events()
    )


def test_takeover_succeeds_at_exact_expiry_instant(
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
    expires_at = first.expires_at
    clock.set(expires_at)

    # 到期瞬间旧 owner 失权且新 owner 可接管，二者必须共用 <= 边界。
    with store._tx():
        with pytest.raises(StaleWriterRejected):
            store._check_ownership_in_tx(
                "ep-1", first.lease_id, first.owner, first.fencing_token
            )
    second = store.acquire_lease(
        resource_type="episode_ownership",
        resource_id="ep-1",
        owner="runner-B",
        ttl_seconds=60,
    )
    assert second.fencing_token > first.fencing_token
