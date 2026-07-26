"""从固定 journal 边界重建调度输入。"""

from __future__ import annotations

from dataclasses import replace

from trowel_py.model_os.scheduling.models import (
    ScheduleCandidate,
    ScheduleInput,
)
from trowel_py.model_os.types import (
    EpisodeStatus,
    EventKind,
    TaskStatus,
    WorkItemKind,
    WorkItemStatus,
)


def _latest_ready_event(events, task_id: str):
    ready = None
    for seq, event in events:
        if event.task_id != task_id:
            continue
        if event.kind == EventKind.TASK_WAITING_CLEARED or (
            event.kind == EventKind.TASK_STATUS_CHANGED
            and event.payload.get("new_status") == TaskStatus.READY.value
        ):
            ready = (seq, event)
    return ready


def _recorded_service(decisions, task_id: str, ready_epoch_ref: str):
    recorded = None
    for _, decision in decisions:
        if decision.kind != "attention.schedule":
            continue
        for item in decision.candidates:
            if not isinstance(item, dict):
                continue
            if (
                item.get("task_id") == task_id
                and item.get("ready_epoch_ref") == ready_epoch_ref
            ):
                recorded = (
                    int(item["virtual_service_segments"]),
                    int(item["journal_event_seq"]),
                )
    return recorded


def build_schedule_input(
    store,
    *,
    trigger_event_ref: str,
    user_override_task_id: str | None = None,
) -> ScheduleInput:
    with store._read_tx():
        snapshot = store.read_snapshot()
        events = tuple(store.list_events())
        decisions = tuple(store.list_decisions())
        boundary = store.journal_boundary()

    releases: dict[str, list[int]] = {}
    previous_task_id = None
    for seq, event in events:
        if event.kind == EventKind.FOREGROUND_RELEASED and event.task_id is not None:
            releases.setdefault(event.task_id, []).append(seq)
            previous_task_id = event.task_id

    candidates: list[ScheduleCandidate] = []
    needs_wake_rebase: set[str] = set()
    for task in snapshot.tasks:
        if not task.warm or task.status is not TaskStatus.READY:
            continue
        work_item = next(
            (
                item
                for item in snapshot.work_items
                if item.work_item_id == task.primary_work_item_id
                and item.kind is WorkItemKind.TASK
            ),
            None,
        )
        if work_item is None or work_item.status is not WorkItemStatus.READY:
            continue
        open_episodes = snapshot.non_terminal_episodes_for_work_item(
            work_item.work_item_id
        )
        suspended_episode_id = None
        if open_episodes:
            if len(open_episodes) != 1:
                continue
            episode = open_episodes[0]
            if (
                episode.status is not EpisodeStatus.SUSPENDED_READY
                or episode.reconcile_reason is not None
            ):
                continue
            suspended_episode_id = episode.episode_id

        ready_event = _latest_ready_event(events, task.task_id)
        if ready_event is None:
            continue
        _, event = ready_event
        ready_epoch_ref = event.event_id
        recorded = _recorded_service(decisions, task.task_id, ready_epoch_ref)
        if recorded is None:
            service = len(releases.get(task.task_id, ()))
            if event.kind == EventKind.TASK_WAITING_CLEARED:
                needs_wake_rebase.add(task.task_id)
        else:
            base, event_seq = recorded
            service = base + sum(
                seq > event_seq for seq in releases.get(task.task_id, ())
            )
        candidates.append(
            ScheduleCandidate(
                work_item_id=work_item.work_item_id,
                task_id=task.task_id,
                priority=task.priority,
                warm_rank=task.warm_rank,
                created_at=task.created_at,
                ready_epoch_ref=ready_epoch_ref,
                virtual_service_segments=service,
                suspended_episode_id=suspended_episode_id,
            )
        )

    if candidates:
        stable = [
            item.virtual_service_segments
            for item in candidates
            if item.task_id not in needs_wake_rebase
        ]
        floor = min(
            stable, default=min(item.virtual_service_segments for item in candidates)
        )
        candidates = [
            replace(item, virtual_service_segments=floor)
            if item.task_id in needs_wake_rebase
            else item
            for item in candidates
        ]

    return ScheduleInput(
        trigger_event_ref=trigger_event_ref,
        journal_boundary=boundary,
        candidates=tuple(candidates),
        current_foreground_task_id=snapshot.foreground_task_id,
        previous_foreground_task_id=previous_task_id,
        user_override_task_id=user_override_task_id,
    )
