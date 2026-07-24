from __future__ import annotations

from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import EpisodeStatus

from tests.model_os._episode_helpers import make_running_task_episode


def test_fail_episode_returns_task_to_ready_and_releases_foreground(
    store: ModelOsStore, monkeypatch
) -> None:
    from tests.model_os._episode_helpers import (
        FakeClock,
        activate_episode,
    )
    from trowel_py.model_os.types import TaskStatus, WorkItemStatus

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, task, work_item_id = make_running_task_episode(store)
    activate_episode(store, episode.episode_id, lease)
    assert store.read_foreground_task_id() == task.task_id

    store.fail_episode(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        reason="tool broke",
    )

    snap = store.read_snapshot()
    assert snap.episode_by_id(episode.episode_id).status == EpisodeStatus.FAILED
    task_state = next(t for t in snap.tasks if t.task_id == task.task_id)
    assert task_state.status == TaskStatus.READY, (
        "Episode failure must return the Task to READY (retryable), not error"
    )
    wi = next(w for w in snap.work_items if w.work_item_id == work_item_id)
    assert wi.status == WorkItemStatus.READY
    assert store.read_foreground_task_id() is None, (
        "Episode failure must release the foreground so another Episode can run"
    )
    row = store._read_episode_lease_row(episode.episode_id)
    assert row is None or row["released_at"] is not None


def test_fail_episode_system_work_item_restores_running(
    store: ModelOsStore, monkeypatch
) -> None:
    from tests.model_os._episode_helpers import (
        FakeClock,
        activate_episode,
        make_running_system_episode,
    )
    from trowel_py.model_os.types import WorkItemStatus

    clock = FakeClock()
    clock.install(monkeypatch)
    episode, lease, work_item_id = make_running_system_episode(store)
    activate_episode(store, episode.episode_id, lease)

    store.fail_episode(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        reason="broke",
    )
    snap = store.read_snapshot()
    assert snap.episode_by_id(episode.episode_id).status == EpisodeStatus.FAILED
    wi = next(w for w in snap.work_items if w.work_item_id == work_item_id)
    assert wi.status == WorkItemStatus.READY
