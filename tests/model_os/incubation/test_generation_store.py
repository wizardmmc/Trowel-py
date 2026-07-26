from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trowel_py.model_os.candidates import CandidateStatus
from trowel_py.model_os.incubation import (
    IncubationError,
    IncubationCandidateDraft,
    IncubationPlanStatus,
    IncubationRepository,
    IncubationUsage,
)
from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import TaskStatus, WorkItemStatus
from trowel_py.model_os.waking import WakeConditionKind, WakeObservation

from tests.model_os.incubation.support import create_command, prepared_running_task


NOW = datetime(2026, 7, 26, 9, 0, tzinfo=timezone.utc)
USAGE = IncubationUsage(120, 30, 4.5, 0.02)


def _ready(repository: IncubationRepository, task_id: str, ref):
    plan = repository.create_plan(create_command(task_id, ref))
    repository.consume_wake(
        WakeObservation(
            observation_id=f"manual:{plan.plan_id}",
            kind=WakeConditionKind.MANUAL,
            target_ref=plan.plan_id,
            observed_at=NOW.isoformat(),
            source="user",
            details={},
        )
    )
    return repository.begin_run(plan.plan_id, occurred_at=NOW)


def test_candidate_commit_is_single_cycle_and_waits_for_user(store: ModelOsStore) -> None:
    task, ref = prepared_running_task(store)
    repository = IncubationRepository(store)
    plan = _ready(repository, task.task_id, ref)
    draft = IncubationCandidateDraft(
        proposal="先验证最小假设",
        source_refs=("snapshot:prepared",),
        new_points=("原方案依赖一个尚未验证的前提",),
        verification="做一个最小复现",
        uncertainty="真实数据可能不同",
    )

    first = repository.commit_generation(
        plan.plan_id,
        draft,
        effective_model="deep-model",
        usage=USAGE,
        occurred_at=NOW,
    )
    replay = repository.commit_generation(
        plan.plan_id,
        draft,
        effective_model="deep-model",
        usage=USAGE,
        occurred_at=NOW,
    )

    assert replay == first
    assert first.plan.status is IncubationPlanStatus.AWAITING_REVIEW
    assert first.plan.cycle == first.plan.max_scheduled_cycles == 1
    assert first.candidate is not None
    assert first.candidate.status is CandidateStatus.NEW
    assert first.candidate.shown_at is None
    shown = repository.mark_candidate_shown(plan.plan_id, occurred_at=NOW)
    assert shown is not None
    assert shown.status is CandidateStatus.SHOWN
    assert shown.shown_at == NOW.isoformat()
    assert repository.candidate_count() == 1
    snapshot = store.read_snapshot()
    current_task = next(item for item in snapshot.tasks if item.task_id == task.task_id)
    incubation_work = next(
        item for item in snapshot.work_items if item.work_item_id == plan.work_item_id
    )
    assert current_task.status is TaskStatus.WAITING_USER
    assert incubation_work.status is WorkItemStatus.DONE


def test_no_increment_stops_and_restores_task(store: ModelOsStore) -> None:
    task, ref = prepared_running_task(store)
    repository = IncubationRepository(store)
    plan = _ready(repository, task.task_id, ref)

    result = repository.commit_generation(
        plan.plan_id,
        IncubationCandidateDraft("", (), (), "", ""),
        effective_model="deep-model",
        usage=USAGE,
        occurred_at=NOW,
    )

    assert result.candidate is None
    assert result.plan.status is IncubationPlanStatus.STOPPED
    assert result.plan.stop_reason == "no_increment"
    task_after = next(
        item for item in store.read_snapshot().tasks if item.task_id == task.task_id
    )
    assert task_after.status is TaskStatus.READY


def test_deadline_failure_is_committed_before_error_is_returned(
    store: ModelOsStore,
) -> None:
    task, ref = prepared_running_task(store)
    repository = IncubationRepository(store)
    plan = repository.create_plan(create_command(task.task_id, ref))
    repository.consume_wake(
        WakeObservation(
            observation_id=f"manual:{plan.plan_id}:deadline",
            kind=WakeConditionKind.MANUAL,
            target_ref=plan.plan_id,
            observed_at=NOW.isoformat(),
            source="user",
            details={},
        )
    )

    with pytest.raises(IncubationError) as raised:
        repository.begin_run(
            plan.plan_id,
            occurred_at=datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc),
        )

    assert raised.value.code == "deadline_expired"
    stopped = repository.get_plan(plan.plan_id)
    assert stopped.status is IncubationPlanStatus.STOPPED
    assert stopped.stop_reason == "deadline_expired"
    snapshot = store.read_snapshot()
    current_task = next(item for item in snapshot.tasks if item.task_id == task.task_id)
    work_item = next(
        item for item in snapshot.work_items if item.work_item_id == plan.work_item_id
    )
    assert current_task.status is TaskStatus.READY
    assert work_item.status is WorkItemStatus.CANCELLED


def test_outcome_is_idempotent_and_cleanup_preserves_snapshot(
    store: ModelOsStore,
) -> None:
    task, ref = prepared_running_task(store)
    repository = IncubationRepository(store)
    plan = _ready(repository, task.task_id, ref)
    generated = repository.commit_generation(
        plan.plan_id,
        IncubationCandidateDraft(
            "候选方案",
            ("snapshot:prepared",),
            ("一个新点",),
            "验证方式",
            "不确定性",
        ),
        effective_model="deep-model",
        usage=USAGE,
        occurred_at=NOW,
    )
    assert generated.candidate is not None

    first = repository.record_outcome(
        command_id="outcome-1",
        candidate_id=generated.candidate.candidate_id,
        outcome="invalid",
        reason="引用不成立",
        occurred_at=NOW,
    )
    replay = repository.record_outcome(
        command_id="outcome-1",
        candidate_id=generated.candidate.candidate_id,
        outcome="invalid",
        reason="引用不成立",
        occurred_at=NOW,
    )
    cleaned = repository.cleanup_artifacts(
        command_id="cleanup-1",
        before="2026-07-27T00:00:00+00:00",
        occurred_at=NOW,
    )

    assert replay == first
    assert first.status is CandidateStatus.INVALID
    assert cleaned == 1
    tombstone = repository.get_candidate(first.candidate_id)
    assert tombstone.status is CandidateStatus.INVALID
    assert tombstone.proposal == ""
    assert tombstone.new_points == ()
    assert store.read_episode_snapshot(ref).current_judgment


def test_generation_does_not_overwrite_a_new_task_wait_condition(
    store: ModelOsStore,
) -> None:
    task, ref = prepared_running_task(store)
    repository = IncubationRepository(store)
    plan = _ready(repository, task.task_id, ref)
    store.clear_waiting(task.task_id)
    store.claim_foreground(task.task_id)
    store.set_waiting_event(
        task.task_id,
        cause="用户后来改为等待文件",
        condition_kind="file",
        target_ref="file:/tmp/new-evidence",
        match_params={"state": "exists"},
    )

    result = repository.commit_generation(
        plan.plan_id,
        IncubationCandidateDraft(
            "旧候选",
            ("snapshot:prepared",),
            ("旧上下文中的新点",),
            "旧验证",
            "旧不确定性",
        ),
        effective_model="deep-model",
        usage=USAGE,
        occurred_at=NOW,
    )

    assert result.plan.status is IncubationPlanStatus.STOPPED
    assert result.plan.stop_reason == "task_changed"
    assert result.candidate is None
    current = next(
        item for item in store.read_snapshot().tasks if item.task_id == task.task_id
    )
    assert current.status is TaskStatus.WAITING_EVENT
    assert current.waiting_condition is not None
    assert current.waiting_condition.target_ref == "file:/tmp/new-evidence"


def test_outcome_does_not_clear_a_new_task_wait_condition(store: ModelOsStore) -> None:
    task, ref = prepared_running_task(store)
    repository = IncubationRepository(store)
    plan = _ready(repository, task.task_id, ref)
    generated = repository.commit_generation(
        plan.plan_id,
        IncubationCandidateDraft(
            "候选",
            ("snapshot:prepared",),
            ("新点",),
            "验证",
            "不确定性",
        ),
        effective_model="deep-model",
        usage=USAGE,
        occurred_at=NOW,
    )
    assert generated.candidate is not None
    store.clear_waiting(task.task_id)
    store.claim_foreground(task.task_id)
    store.set_waiting_event(
        task.task_id,
        cause="用户后来改为等待文件",
        condition_kind="file",
        target_ref="file:/tmp/new-evidence",
        match_params={"state": "exists"},
    )

    with pytest.raises(IncubationError) as raised:
        repository.record_outcome(
            command_id="stale-outcome",
            candidate_id=generated.candidate.candidate_id,
            outcome="adopted",
            reason=None,
            occurred_at=NOW,
        )
    with pytest.raises(IncubationError) as replayed:
        repository.record_outcome(
            command_id="stale-outcome",
            candidate_id=generated.candidate.candidate_id,
            outcome="adopted",
            reason=None,
            occurred_at=NOW,
        )

    assert raised.value.code == "task_changed"
    assert replayed.value.code == "task_changed"
    assert repository.get_plan(plan.plan_id).stop_reason == "task_changed"
    current = next(
        item for item in store.read_snapshot().tasks if item.task_id == task.task_id
    )
    assert current.status is TaskStatus.WAITING_EVENT
    assert current.waiting_condition is not None
    assert current.waiting_condition.target_ref == "file:/tmp/new-evidence"


def test_cleanup_compares_timezone_offsets_as_utc_instants(store: ModelOsStore) -> None:
    task, ref = prepared_running_task(store)
    repository = IncubationRepository(store)
    plan = _ready(repository, task.task_id, ref)
    generated = repository.commit_generation(
        plan.plan_id,
        IncubationCandidateDraft(
            "候选", ("snapshot:prepared",), ("新点",), "验证", "不确定性"
        ),
        effective_model="deep-model",
        usage=USAGE,
        occurred_at=NOW,
    )
    assert generated.candidate is not None
    repository.record_outcome(
        command_id="timezone-outcome",
        candidate_id=generated.candidate.candidate_id,
        outcome="invalid",
        reason="引用不成立",
        occurred_at=NOW,
    )

    too_early = repository.cleanup_artifacts(
        command_id="timezone-cleanup-early",
        before="2026-07-26T10:00:00+08:00",
        occurred_at=NOW,
    )
    cleaned = repository.cleanup_artifacts(
        command_id="timezone-cleanup-late",
        before="2026-07-26T18:00:00+08:00",
        occurred_at=NOW,
    )

    assert too_early == 0
    assert cleaned == 1
