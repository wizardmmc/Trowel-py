from __future__ import annotations

import pytest

from trowel_py.model_os.store import EpisodeCommandError, ModelOsStore
from trowel_py.model_os.types import EpisodeStatus

from tests.model_os._episode_helpers import activate_episode


def test_start_episode_rejects_unknown_work_item(store: ModelOsStore) -> None:
    with pytest.raises(EpisodeCommandError):
        store.start_episode(
            work_item_id="does-not-exist",
            owner="runner-A",
            ttl_seconds=300,
            idempotency_key="ep-dangling",
        )


def test_suspend_rejects_when_task_not_running(
    store: ModelOsStore, monkeypatch
) -> None:
    from tests.model_os._episode_helpers import FakeClock
    from trowel_py.model_os.types import PendingDescriptor, WaitingSubtype

    clock = FakeClock()
    clock.install(monkeypatch)
    task = store.create_task_from_user_request(
        original_goal="g", idempotency_key="tk-1"
    )
    store.promote_to_warm(task.task_id)
    work_item_id = task.primary_work_item_id
    episode, lease = store.start_episode(
        work_item_id=work_item_id,
        owner="runner-A",
        ttl_seconds=300,
        idempotency_key="ep-1",
        task_id=task.task_id,
    )
    activate_episode(store, episode.episode_id, lease)
    with pytest.raises(EpisodeCommandError):
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
    assert (
        store.read_snapshot().episode_by_id(episode.episode_id).status
        == EpisodeStatus.ACTIVE
    )
