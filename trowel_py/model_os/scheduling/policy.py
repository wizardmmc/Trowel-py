"""带稳定、可回放同分规则的纯注意力调度策略。"""

from __future__ import annotations

from dataclasses import replace

from trowel_py.model_os.scheduling.models import (
    ScheduleAction,
    ScheduleCandidate,
    ScheduleDecision,
    ScheduleInput,
    ScheduleReason,
)


def rebase_ready_candidate(
    candidate: ScheduleCandidate,
    runnable: tuple[ScheduleCandidate, ...],
) -> ScheduleCandidate:
    floor = min(
        (item.virtual_service_segments for item in runnable),
        default=0,
    )
    return replace(candidate, virtual_service_segments=floor)


def _candidate_sort_key(
    candidate: ScheduleCandidate,
    previous_task_id: str | None,
) -> tuple[int, int, int, bool, int, str, str]:
    return (
        candidate.virtual_service_segments,
        -candidate.priority,
        0 if candidate.task_id == previous_task_id else 1,
        candidate.warm_rank is None,
        candidate.warm_rank if candidate.warm_rank is not None else 0,
        candidate.created_at,
        candidate.task_id,
    )


def _decision(
    schedule_input: ScheduleInput,
    *,
    action: ScheduleAction,
    reason: ScheduleReason,
    target: ScheduleCandidate | None = None,
    target_task_id: str | None = None,
) -> ScheduleDecision:
    return ScheduleDecision(
        action=action,
        reason=reason,
        trigger_event_ref=schedule_input.trigger_event_ref,
        journal_boundary=schedule_input.journal_boundary,
        candidate_summaries=schedule_input.candidates,
        target_work_item_id=target.work_item_id if target else None,
        target_task_id=target.task_id if target else target_task_id,
        target_episode_id=target.suspended_episode_id if target else None,
    )


def decide_schedule(schedule_input: ScheduleInput) -> ScheduleDecision:
    by_task_id = {item.task_id: item for item in schedule_input.candidates}
    if len(by_task_id) != len(schedule_input.candidates):
        raise ValueError("scheduling candidates must have unique task IDs")

    current = schedule_input.current_foreground_task_id
    override = schedule_input.user_override_task_id
    if override is not None and override != current and override not in by_task_id:
        raise ValueError("user override target must be a ready scheduling candidate")

    if current is not None:
        if override is None:
            return _decision(
                schedule_input,
                action=ScheduleAction.CONTINUE,
                reason=ScheduleReason.ACTIVE_FOCUS,
                target_task_id=current,
            )
        if override == current:
            return _decision(
                schedule_input,
                action=ScheduleAction.CONTINUE,
                reason=ScheduleReason.USER_OVERRIDE_CURRENT,
                target_task_id=current,
            )
        return _decision(
            schedule_input,
            action=ScheduleAction.REQUEST_YIELD,
            reason=ScheduleReason.USER_OVERRIDE_SWITCH,
            target=by_task_id[override],
        )

    if override is not None:
        return _decision(
            schedule_input,
            action=ScheduleAction.DISPATCH,
            reason=ScheduleReason.USER_OVERRIDE_SWITCH,
            target=by_task_id[override],
        )

    if not schedule_input.candidates:
        return _decision(
            schedule_input,
            action=ScheduleAction.IDLE,
            reason=ScheduleReason.NO_READY_WORK,
        )

    selected = min(
        schedule_input.candidates,
        key=lambda item: _candidate_sort_key(
            item,
            schedule_input.previous_foreground_task_id,
        ),
    )
    return _decision(
        schedule_input,
        action=ScheduleAction.DISPATCH,
        reason=ScheduleReason.FAIR_SERVICE,
        target=selected,
    )
