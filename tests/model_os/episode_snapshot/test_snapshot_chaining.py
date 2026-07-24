from __future__ import annotations

from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import SnapshotRef, Task
from tests.model_os._episode_helpers import (
    FakeClock,
    activate_episode,
    make_cooperative_snapshot,
    make_running_task_episode,
)


def _complete_episode(
    store: ModelOsStore,
    task: Task,
    *,
    previous_snapshot_ref: SnapshotRef | None,
    judgment: str,
    key: str,
) -> SnapshotRef:
    work_item_id = task.primary_work_item_id
    assert work_item_id is not None
    episode, lease = store.start_episode(
        work_item_id=work_item_id,
        owner="runner-A",
        ttl_seconds=300,
        idempotency_key=key,
        task_id=task.task_id,
        previous_snapshot_ref=previous_snapshot_ref,
    )
    activate_episode(store, episode.episode_id, lease)
    store.request_yield(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        reason="done",
    )
    snapshot_ref = store.commit_checkpoint(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        snapshot=make_cooperative_snapshot(current_judgment=judgment),
        checkpoint_key=f"ck-{key}",
    )
    store.close_episode(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
    )
    return snapshot_ref


def test_sequential_episode_snapshots_do_not_copy_previous_payloads(
    store: ModelOsStore, monkeypatch
) -> None:
    clock = FakeClock()
    clock.install(monkeypatch)
    seed, seed_lease, task, _ = make_running_task_episode(
        store, ttl_seconds=300, idempotency_key="ep-seed"
    )
    activate_episode(store, seed.episode_id, seed_lease)
    store.request_yield(
        seed.episode_id,
        expected_lease_id=seed_lease.lease_id,
        expected_owner=seed_lease.owner,
        expected_token=seed_lease.fencing_token,
        reason="seed",
    )
    store.commit_checkpoint(
        seed.episode_id,
        expected_lease_id=seed_lease.lease_id,
        expected_owner=seed_lease.owner,
        expected_token=seed_lease.fencing_token,
        snapshot=make_cooperative_snapshot(current_judgment="seed"),
        checkpoint_key="ck-seed",
    )
    store.close_episode(
        seed.episode_id,
        expected_lease_id=seed_lease.lease_id,
        expected_owner=seed_lease.owner,
        expected_token=seed_lease.fencing_token,
    )

    ref1 = _complete_episode(
        store,
        task,
        previous_snapshot_ref=None,
        judgment="judgment-ONE",
        key="ep-1",
    )
    ref2 = _complete_episode(
        store,
        task,
        previous_snapshot_ref=ref1,
        judgment="judgment-TWO",
        key="ep-2",
    )
    ref3 = _complete_episode(
        store,
        task,
        previous_snapshot_ref=ref2,
        judgment="judgment-THREE",
        key="ep-3",
    )

    latest = store.read_episode_snapshot(ref3)
    connection = store._conn
    assert connection is not None
    latest_payload = connection.execute(
        "SELECT payload_json FROM episode_snapshots WHERE episode_id=? AND version=?",
        (ref3.episode_id, ref3.version),
    ).fetchone()["payload_json"]
    # 给新 Episode 传前序引用时，不得把前序 payload 展开进新快照。
    assert "judgment-THREE" in latest.current_judgment
    assert "judgment-ONE" not in latest_payload
    assert "judgment-TWO" not in latest_payload

    sizes = [
        int(
            connection.execute(
                "SELECT length(payload_json) AS length FROM episode_snapshots "
                "WHERE episode_id=? AND version=?",
                (snapshot_ref.episode_id, snapshot_ref.version),
            ).fetchone()["length"]
        )
        for snapshot_ref in (ref1, ref2, ref3)
    ]
    assert max(sizes) <= min(sizes) * 2
