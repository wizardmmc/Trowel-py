from trowel_py.model_os.scheduling import build_schedule_input
from trowel_py.model_os.store import ModelOsStore


def _task(store: ModelOsStore, name: str, *, priority: int = 0):
    return store.create_task_from_user_request(
        original_goal=f"匿名任务 {name}",
        idempotency_key=f"create-{name}",
        priority=priority,
    )


def test_schedule_input_uses_only_ready_warm_task_work_items(
    store: ModelOsStore,
) -> None:
    tasks = tuple(_task(store, str(index), priority=index) for index in range(4))
    for task in tasks[:3]:
        store.promote_to_warm(task.task_id)
    store.claim_foreground(tasks[0].task_id)
    store.release_foreground()

    schedule_input = build_schedule_input(store, trigger_event_ref="trigger.ready")

    by_id = {item.task_id: item for item in schedule_input.candidates}
    assert set(by_id) == {task.task_id for task in tasks[:3]}
    assert tasks[3].task_id not in by_id
    assert by_id[tasks[0].task_id].virtual_service_segments == 1
    assert by_id[tasks[1].task_id].virtual_service_segments == 0
    assert schedule_input.current_foreground_task_id is None
    assert schedule_input.previous_foreground_task_id == tasks[0].task_id


def test_task_with_open_starting_episode_is_not_a_ready_candidate(
    store: ModelOsStore,
) -> None:
    task = _task(store, "starting")
    store.promote_to_warm(task.task_id)
    store.start_episode(
        work_item_id=task.primary_work_item_id,
        owner="runner",
        ttl_seconds=300,
        idempotency_key="starting-episode",
        task_id=task.task_id,
    )

    schedule_input = build_schedule_input(store, trigger_event_ref="trigger.starting")

    assert schedule_input.candidates == ()


def test_latest_recorded_ready_epoch_preserves_wake_rebase_floor(
    store: ModelOsStore,
) -> None:
    older = _task(store, "older")
    peer = _task(store, "peer")
    for task in (older, peer):
        store.promote_to_warm(task.task_id)
    store.claim_foreground(older.task_id)
    store.set_waiting_event(
        older.task_id,
        cause="等待匿名事件",
        condition_kind="manual",
        target_ref="task:older",
    )
    for _ in range(3):
        store.claim_foreground(peer.task_id)
        store.release_foreground()
    store.clear_waiting(older.task_id)

    first = build_schedule_input(store, trigger_event_ref="trigger.wake.first")
    from trowel_py.model_os.scheduling import decide_schedule, record_schedule_decision

    record_schedule_decision(store, decide_schedule(first))
    store.claim_foreground(peer.task_id)
    store.release_foreground()
    second = build_schedule_input(store, trigger_event_ref="trigger.wake.second")

    first_service = next(
        item.virtual_service_segments
        for item in first.candidates
        if item.task_id == older.task_id
    )
    second_service = next(
        item.virtual_service_segments
        for item in second.candidates
        if item.task_id == older.task_id
    )
    assert first_service == 3
    assert second_service == first_service
