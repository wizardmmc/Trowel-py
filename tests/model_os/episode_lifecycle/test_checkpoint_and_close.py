from __future__ import annotations

import pytest

from trowel_py.model_os.store import EpisodeCommandError, ModelOsStore
from trowel_py.model_os.types import EpisodeSnapshot, EpisodeStatus

from tests.model_os._episode_helpers import (
    FakeClock,
    activate_episode,
    make_cooperative_snapshot,
    make_running_system_episode,
)


def _checkpoint(
    store: ModelOsStore,
    episode_id: str,
    lease,
    *,
    key: str = "ck-1",
    snapshot: EpisodeSnapshot | None = None,
) -> None:
    store.commit_checkpoint(
        episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        snapshot=snapshot or make_cooperative_snapshot(),
        checkpoint_key=key,
    )


def test_commit_checkpoint_always_targets_checkpointing(
    store: ModelOsStore, monkeypatch
) -> None:
    from tests.model_os._episode_helpers import FakeClock

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _ = make_running_system_episode(store)
    activate_episode(store, episode.episode_id, lease)
    store.request_yield(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        reason="winding down",
    )
    _checkpoint(store, episode.episode_id, lease)
    assert (
        store.read_snapshot().episode_by_id(episode.episode_id).status
        == EpisodeStatus.CHECKPOINTING
    )


def test_commit_checkpoint_signature_has_no_arbitrary_next_status() -> None:
    import inspect

    sig = inspect.signature(ModelOsStore.commit_checkpoint)
    assert "next_status" not in sig.parameters, (
        "commit_checkpoint must not accept next_status; the target state is "
        "fixed by the command (checkpointing), not caller-chosen"
    )


def test_commit_checkpoint_refuses_from_reconcile_required(
    store: ModelOsStore, monkeypatch
) -> None:
    from tests.model_os._episode_helpers import FakeClock
    from trowel_py.model_os.types import (
        PendingDescriptor,
        ReconcileReason,
        WaitingSubtype,
    )

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _ = make_running_system_episode(store)
    activate_episode(store, episode.episode_id, lease)
    store.suspend_episode(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        pending=PendingDescriptor(
            kind=WaitingSubtype.INPUT,
            native_generation="g1",
            correlation_id="c1",
            cause="need input",
            posed_at="2026-07-21T00:00:05Z",
        ),
    )
    store.mark_pending_channel_lost(
        episode.episode_id, reason=ReconcileReason.REQUIRES_USER_RESTART
    )
    assert (
        store.read_snapshot().episode_by_id(episode.episode_id).status
        == EpisodeStatus.RECONCILE_REQUIRED
    )

    with pytest.raises(EpisodeCommandError):
        _checkpoint(store, episode.episode_id, lease, key="ck-reconcile")
    assert (
        store.read_snapshot().episode_by_id(episode.episode_id).status
        == EpisodeStatus.RECONCILE_REQUIRED
    )


def test_close_episode_only_from_checkpointing(
    store: ModelOsStore, monkeypatch
) -> None:
    from tests.model_os._episode_helpers import FakeClock

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, _ = make_running_system_episode(store)
    activate_episode(store, episode.episode_id, lease)

    with pytest.raises(EpisodeCommandError):
        store.close_episode(
            episode.episode_id,
            expected_lease_id=lease.lease_id,
            expected_owner=lease.owner,
            expected_token=lease.fencing_token,
        )

    store.request_yield(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        reason="done",
    )
    _checkpoint(store, episode.episode_id, lease)
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


def test_close_episode_releases_ownership_lease(
    store: ModelOsStore, monkeypatch
) -> None:
    from tests.model_os._episode_helpers import FakeClock

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
    _checkpoint(store, episode.episode_id, lease)
    store.close_episode(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
    )
    row = store._read_episode_lease_row(episode.episode_id)
    assert row is None or row["released_at"] is not None, (
        "closed Episode must not keep an active ownership lease"
    )


def test_commit_checkpoint_rejects_future_journal_sequence(
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
    live_end = store.read_snapshot().last_seq
    future_snapshot = make_cooperative_snapshot(journal_through_seq=live_end + 999)
    # 未来序号会使后续恢复跳过尚未写入的事件。
    with pytest.raises(EpisodeCommandError):
        store.commit_checkpoint(
            episode.episode_id,
            expected_lease_id=lease.lease_id,
            expected_owner=lease.owner,
            expected_token=lease.fencing_token,
            snapshot=future_snapshot,
            checkpoint_key="ck-future",
        )
