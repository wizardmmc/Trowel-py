from dataclasses import replace

import pytest

from trowel_py.model_os.journal import JournalBoundary
from trowel_py.model_os.scheduling import (
    ATTENTION_POLICY_VERSION,
    ScheduleAction,
    ScheduleCandidate,
    ScheduleInput,
    ScheduleReason,
    decide_schedule,
    rebase_ready_candidate,
)


def candidate(
    task_id: str,
    *,
    service: int = 0,
    priority: int = 0,
    warm_rank: int | None = None,
    created_at: str = "2026-07-26T00:00:00Z",
) -> ScheduleCandidate:
    return ScheduleCandidate(
        work_item_id=f"work-{task_id}",
        task_id=task_id,
        priority=priority,
        warm_rank=warm_rank,
        created_at=created_at,
        ready_epoch_ref=f"ready-{task_id}",
        virtual_service_segments=service,
    )


def schedule_input(
    candidates: tuple[ScheduleCandidate, ...] = (),
    *,
    current: str | None = None,
    previous: str | None = None,
    override: str | None = None,
) -> ScheduleInput:
    return ScheduleInput(
        trigger_event_ref="event-1",
        journal_boundary=JournalBoundary(event_seq=7, decision_seq=3),
        candidates=candidates,
        current_foreground_task_id=current,
        previous_foreground_task_id=previous,
        user_override_task_id=override,
    )


def test_active_focus_continues_even_when_a_higher_priority_task_wakes() -> None:
    decision = decide_schedule(
        schedule_input((candidate("woken", priority=100),), current="current")
    )

    assert decision.action is ScheduleAction.CONTINUE
    assert decision.target_task_id == "current"
    assert decision.reason is ScheduleReason.ACTIVE_FOCUS
    assert decision.policy_version == ATTENTION_POLICY_VERSION


def test_override_of_current_foreground_is_idempotent() -> None:
    decision = decide_schedule(schedule_input(current="current", override="current"))

    assert decision.action is ScheduleAction.CONTINUE
    assert decision.target_task_id == "current"
    assert decision.reason is ScheduleReason.USER_OVERRIDE_CURRENT


def test_override_of_another_ready_task_requests_safe_yield() -> None:
    decision = decide_schedule(
        schedule_input((candidate("chosen"),), current="current", override="chosen")
    )

    assert decision.action is ScheduleAction.REQUEST_YIELD
    assert decision.target_task_id == "chosen"
    assert decision.reason is ScheduleReason.USER_OVERRIDE_SWITCH


def test_override_wins_at_a_safe_boundary() -> None:
    decision = decide_schedule(
        schedule_input((candidate("chosen"),), previous="current", override="chosen")
    )

    assert decision.action is ScheduleAction.DISPATCH
    assert decision.target_task_id == "chosen"
    assert decision.target_work_item_id == "work-chosen"
    assert decision.reason is ScheduleReason.USER_OVERRIDE_SWITCH


def test_no_ready_work_is_idle() -> None:
    decision = decide_schedule(schedule_input())

    assert decision.action is ScheduleAction.IDLE
    assert decision.target_task_id is None
    assert decision.reason is ScheduleReason.NO_READY_WORK


def test_priority_orders_equal_service_without_starving_lower_priority() -> None:
    candidates = {
        item.task_id: item
        for item in (
            candidate("high", priority=100),
            candidate("normal"),
            candidate("low", priority=-100),
        )
    }
    chosen: list[str] = []
    previous = None
    last_seen = {task_id: -1 for task_id in candidates}

    for turn in range(30):
        decision = decide_schedule(
            schedule_input(tuple(candidates.values()), previous=previous)
        )
        assert decision.target_task_id is not None
        task_id = decision.target_task_id
        chosen.append(task_id)
        last_seen[task_id] = turn
        candidates[task_id] = replace(
            candidates[task_id],
            virtual_service_segments=(candidates[task_id].virtual_service_segments + 1),
        )
        previous = task_id

        services = [item.virtual_service_segments for item in candidates.values()]
        assert max(services) - min(services) <= 1
        if turn >= 2:
            assert turn - min(last_seen.values()) <= 2

    assert chosen[:3] == ["high", "normal", "low"]


def test_stable_tie_breakers_prefer_previous_then_rank_creation_and_id() -> None:
    candidates = (
        candidate("rank-2", warm_rank=2),
        candidate("previous", warm_rank=9),
        candidate("rank-1-new", warm_rank=1, created_at="2026-07-26T00:00:01Z"),
        candidate("rank-1-old-b", warm_rank=1),
        candidate("rank-1-old-a", warm_rank=1),
    )

    first = decide_schedule(schedule_input(candidates, previous="previous"))
    without_previous = decide_schedule(schedule_input(candidates))

    assert first.target_task_id == "previous"
    assert without_previous.target_task_id == "rank-1-old-a"


def test_missing_warm_rank_sorts_after_any_integer_rank() -> None:
    decision = decide_schedule(
        schedule_input(
            (
                candidate("missing-rank"),
                candidate("large-rank", warm_rank=2_000_000_000),
            )
        )
    )

    assert decision.target_task_id == "large-rank"


def test_newly_ready_task_rebases_to_the_current_service_floor() -> None:
    waking = candidate("waking", service=2)
    runnable = (candidate("a", service=100), candidate("b", service=101))

    rebased = rebase_ready_candidate(waking, runnable)

    assert rebased.virtual_service_segments == 100
    assert rebase_ready_candidate(waking, ()).virtual_service_segments == 0


def test_override_target_must_be_current_or_a_ready_candidate() -> None:
    with pytest.raises(ValueError, match="ready scheduling candidate"):
        decide_schedule(schedule_input(current="current", override="waiting"))
