from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests.model_os.waking.support import running_task
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import TaskStatus, WorkItemStatus
from trowel_py.model_os.waking import (
    WakeConditionKind,
    WakeDisposition,
    WakeObservation,
)


def _observation(
    observation_id: str,
    *,
    kind: WakeConditionKind = WakeConditionKind.OBSERVED_STATE,
    target_ref: str = "file:/tmp/build.done",
    observed_at: str = "2099-01-01T09:00:00Z",
    details: dict | None = None,
) -> WakeObservation:
    return WakeObservation(
        observation_id=observation_id,
        kind=kind,
        target_ref=target_ref,
        observed_at=observed_at,
        source="test-observer",
        details=details or {"state_kind": "file", "state": "exists"},
    )


def test_wait_registration_adds_stable_condition_identity(store: ModelOsStore) -> None:
    task = running_task(store)
    store.set_waiting_event(
        task.task_id,
        cause="等待构建产物",
        condition_kind="file",
        target_ref="file:/tmp/build.done",
        match_params={"state": "exists"},
    )

    waiting = store.read_snapshot().tasks[0].waiting_condition

    assert waiting is not None
    assert waiting.condition_id.startswith("wake.condition.")
    assert waiting.registered_at
    assert waiting.catchup_policy == "no_catchup"


def test_matching_observation_atomically_readies_task_and_work_item(
    store: ModelOsStore,
) -> None:
    task = running_task(store)
    store.set_waiting_event(
        task.task_id,
        cause="等待构建产物",
        condition_kind="file",
        target_ref="file:/tmp/build.done",
        match_params={"state": "exists"},
    )

    events = store.consume_wake(_observation("file-observation-1"))

    assert len(events) == 1
    assert events[0].disposition is WakeDisposition.READY
    snapshot = store.read_snapshot()
    ready = next(item for item in snapshot.tasks if item.task_id == task.task_id)
    work_item = next(
        item for item in snapshot.work_items if item.work_item_id == task.primary_work_item_id
    )
    assert ready.status is TaskStatus.READY
    assert ready.waiting_condition is None
    assert work_item.status is WorkItemStatus.READY
    assert snapshot.unrecognized_event_kinds == ()


def test_same_observation_returns_original_event_after_wait_is_cleared(
    store: ModelOsStore,
) -> None:
    task = running_task(store)
    store.set_waiting_event(
        task.task_id,
        cause="等待构建产物",
        condition_kind="file",
        target_ref="file:/tmp/build.done",
        match_params={"state": "exists"},
    )
    observation = _observation("file-observation-repeat")

    first = store.consume_wake(observation)
    second = store.consume_wake(observation)

    assert second == first
    assert len(
        [
            event
            for _, event in store.list_events()
            if event.kind == "wake.consumed"
            and event.payload.get("observation_id") == observation.observation_id
        ]
    ) == 1


def test_duplicate_observation_remains_idempotent_after_store_reopen(
    db_path: Path,
) -> None:
    first_store = ModelOsStore(db_path)
    first_store.open()
    task = running_task(first_store)
    first_store.set_waiting_event(
        task.task_id,
        cause="等待构建产物",
        condition_kind="file",
        target_ref="file:/tmp/build.done",
        match_params={"state": "exists"},
    )
    observation = _observation("file-observation-reopen")
    first = first_store.consume_wake(observation)
    first_store.close()

    reopened = ModelOsStore(db_path)
    reopened.open()
    try:
        assert reopened.consume_wake(observation) == first
    finally:
        reopened.close()


def test_two_connections_cannot_consume_same_condition_twice(db_path: Path) -> None:
    bootstrap = ModelOsStore(db_path)
    bootstrap.open()
    task = running_task(bootstrap)
    bootstrap.set_waiting_event(
        task.task_id,
        cause="等待构建产物",
        condition_kind="file",
        target_ref="file:/tmp/build.done",
        match_params={"state": "exists"},
    )
    bootstrap.close()
    observation = _observation("file-observation-race")

    def consume() -> tuple:
        candidate = ModelOsStore(db_path)
        candidate.open()
        try:
            return candidate.consume_wake(observation)
        finally:
            candidate.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: consume(), range(2)))

    assert results[0] == results[1]
    check = ModelOsStore(db_path)
    check.open()
    try:
        assert len(
            [
                event
                for _, event in check.list_events()
                if event.kind == "wake.consumed"
                and event.payload.get("observation_id") == observation.observation_id
            ]
        ) == 1
    finally:
        check.close()


@pytest.mark.parametrize(
    "observation",
    [
        _observation("wrong-target", target_ref="file:/tmp/other"),
        _observation(
            "wrong-state",
            details={"state_kind": "file", "state": "missing"},
        ),
        _observation(
            "stale",
            observed_at="2026-07-26T09:00:00Z",
            details={
                "state_kind": "file",
                "state": "exists",
                "fresh_until": "2026-07-26T08:59:59Z",
            },
        ),
    ],
)
def test_wrong_or_stale_observation_does_not_wake(
    store: ModelOsStore,
    observation: WakeObservation,
) -> None:
    task = running_task(store)
    store.set_waiting_event(
        task.task_id,
        cause="等待构建产物",
        condition_kind="file",
        target_ref="file:/tmp/build.done",
        match_params={"state": "exists"},
    )

    assert store.consume_wake(observation) == ()
    assert store.read_snapshot().tasks[0].status is TaskStatus.WAITING_EVENT
