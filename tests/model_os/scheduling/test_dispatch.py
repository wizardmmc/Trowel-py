from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path

import pytest

from trowel_py.model_os.scheduling import (
    build_schedule_input,
    decide_schedule,
    record_schedule_decision,
)
from trowel_py.model_os.store import EpisodeCommandError, ModelOsStore
from trowel_py.model_os.types import EventKind, TaskStatus, WorkItemStatus


def _prepared_dispatch(store: ModelOsStore, name: str):
    task = store.create_task_from_user_request(
        original_goal=f"匿名任务 {name}",
        idempotency_key=f"create-{name}",
    )
    store.promote_to_warm(task.task_id)
    schedule = decide_schedule(
        build_schedule_input(store, trigger_event_ref=f"trigger.{name}")
    )
    recorded = record_schedule_decision(store, schedule)
    episode, ownership = store.start_episode(
        work_item_id=task.primary_work_item_id,
        owner="attention-scheduler",
        ttl_seconds=300,
        idempotency_key=f"episode-{name}",
        task_id=task.task_id,
    )
    return task, recorded, episode, ownership


def test_scheduled_claim_atomically_sets_all_foreground_facts(
    store: ModelOsStore,
) -> None:
    task, recorded, episode, ownership = _prepared_dispatch(store, "one")

    store.claim_scheduled_foreground(
        decision_id=recorded.decision_id,
        task_id=task.task_id,
        episode_id=episode.episode_id,
        expected_lease_id=ownership.lease_id,
        expected_owner=ownership.owner,
        expected_token=ownership.fencing_token,
    )
    store.claim_scheduled_foreground(
        decision_id=recorded.decision_id,
        task_id=task.task_id,
        episode_id=episode.episode_id,
        expected_lease_id=ownership.lease_id,
        expected_owner=ownership.owner,
        expected_token=ownership.fencing_token,
    )

    snapshot = store.read_snapshot()
    task_state = next(item for item in snapshot.tasks if item.task_id == task.task_id)
    work_item = next(
        item
        for item in snapshot.work_items
        if item.work_item_id == task.primary_work_item_id
    )
    assert snapshot.foreground_task_id == task.task_id
    assert task_state.status is TaskStatus.RUNNING
    assert work_item.status is WorkItemStatus.RUNNING
    claimed = [
        event
        for _, event in store.list_events()
        if event.kind == EventKind.FOREGROUND_CLAIMED and event.task_id == task.task_id
    ]
    assert len(claimed) == 1


def test_scheduled_claim_rejects_a_decision_for_another_task(
    store: ModelOsStore,
) -> None:
    task, recorded, episode, ownership = _prepared_dispatch(store, "one")
    other = store.create_task_from_user_request(
        original_goal="另一个匿名任务",
        idempotency_key="create-other",
    )

    with pytest.raises(EpisodeCommandError, match="decision target"):
        store.claim_scheduled_foreground(
            decision_id=recorded.decision_id,
            task_id=other.task_id,
            episode_id=episode.episode_id,
            expected_lease_id=ownership.lease_id,
            expected_owner=ownership.owner,
            expected_token=ownership.fencing_token,
        )
    assert store.read_snapshot().foreground_task_id is None


def test_two_connections_cannot_claim_different_scheduled_tasks(
    db_path: Path,
) -> None:
    with ExitStack() as stack:
        first = ModelOsStore(db_path)
        first.open()
        stack.callback(first.close)
        task_a, decision_a, episode_a, ownership_a = _prepared_dispatch(first, "a")

        second = ModelOsStore(db_path)
        second.open()
        stack.callback(second.close)
        task_b, decision_b, episode_b, ownership_b = _prepared_dispatch(second, "b")

        def claim(store, task, decision, episode, ownership):
            try:
                store.claim_scheduled_foreground(
                    decision_id=decision.decision_id,
                    task_id=task.task_id,
                    episode_id=episode.episode_id,
                    expected_lease_id=ownership.lease_id,
                    expected_owner=ownership.owner,
                    expected_token=ownership.fencing_token,
                )
            except Exception as exc:
                return type(exc).__name__
            return "ok"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = tuple(
                future.result()
                for future in (
                    pool.submit(
                        claim,
                        first,
                        task_a,
                        decision_a,
                        episode_a,
                        ownership_a,
                    ),
                    pool.submit(
                        claim,
                        second,
                        task_b,
                        decision_b,
                        episode_b,
                        ownership_b,
                    ),
                )
            )

        assert results.count("ok") == 1
        assert first.read_snapshot().foreground_task_id in {
            task_a.task_id,
            task_b.task_id,
        }
