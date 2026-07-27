from __future__ import annotations

from trowel_py.model_os.workbench import (
    automation_is_paused,
    build_workbench_state,
    set_automation_paused,
)
from trowel_py.model_os.candidates import CandidateStatus
from trowel_py.model_os.default_work import Candidate
from trowel_py.model_os.incubation import (
    IncubationCandidate,
    IncubationPlanStatus,
)


def _task(store, goal: str, key: str, *, priority: int):
    return store.create_task_from_user_request(
        original_goal=goal,
        idempotency_key=key,
        priority=priority,
    )


def test_workbench_uses_snapshot_for_foreground_and_policy_for_next_task(store) -> None:
    current = _task(store, "完成工作台", "task-current", priority=5)
    next_task = _task(store, "补齐浏览器验证", "task-next", priority=8)
    later = _task(store, "整理发布记录", "task-later", priority=1)
    for task in (current, next_task, later):
        store.promote_to_warm(task.task_id)
    store.claim_foreground(current.task_id)

    state = build_workbench_state(store)

    assert state.foreground_task_id == current.task_id
    assert state.next_task_id == next_task.task_id
    assert [task.task_id for task in state.tasks[:3]] == [
        current.task_id,
        next_task.task_id,
        later.task_id,
    ]
    assert state.tasks[0].is_foreground is True
    assert state.tasks[0].goal == "完成工作台"
    assert state.tasks[0].can_send_message is False
    assert state.as_of.event_seq > 0
    assert state.recent_events


def test_automation_pause_is_persistent_and_idempotent(store) -> None:
    first = set_automation_paused(
        store,
        paused=True,
        idempotency_key="pause-workbench",
    )
    repeated = set_automation_paused(
        store,
        paused=True,
        idempotency_key="pause-workbench",
    )

    assert first is True
    assert repeated is True
    assert automation_is_paused(store) is True
    assert "automation.mode_changed" not in (
        store.read_snapshot().unrecognized_event_kinds
    )

    set_automation_paused(
        store,
        paused=False,
        idempotency_key="resume-workbench",
    )
    assert automation_is_paused(store) is False


class _DefaultRepository:
    def pending_candidates(self):
        return (
            Candidate(
                candidate_id="default-candidate",
                generation_id="generation-1",
                content="把等待原因收敛成稳定分类",
                source_refs=("memory://notes/a",),
                related_question="怎样减少状态误判",
                why_useful="页面可以直接解释等待原因",
                verification="用真实等待状态逐一走查",
                uncertainty="暂不确定是否需要单列资源等待",
                runtime="codex",
                effective_model="deep-model",
                tier="deep",
                policy_version="default-policy",
                status=CandidateStatus.NEW,
                created_at="2026-07-27T01:00:00+00:00",
            ),
        )


class _Plan:
    task_id = "task-incubating"
    unresolved_question = "是否应继续实现"
    status = IncubationPlanStatus.AWAITING_REVIEW


class _IncubationRepository:
    def pending_review_candidates(self):
        return (
            (
                _Plan(),
                IncubationCandidate(
                    candidate_id="incubation-candidate",
                    plan_id="plan-1",
                    cycle=1,
                    proposal="先冻结接口再接事件流",
                    source_refs=("event://decision/a",),
                    new_points=("刷新和实时必须共用同一投影",),
                    verification="刷新后核对同一事件水位",
                    uncertainty="长连接恢复仍需真实浏览器确认",
                    runtime="claude_code",
                    effective_model="deep-model",
                    tier="deep",
                    policy_version="incubation-policy",
                    status=CandidateStatus.SHOWN,
                    created_at="2026-07-27T02:00:00+00:00",
                ),
            ),
        )


def test_workbench_keeps_default_and_incubation_candidates_distinct(store) -> None:
    state = build_workbench_state(
        store,
        default_repository=_DefaultRepository(),
        incubation_repository=_IncubationRepository(),
    )

    assert [candidate["source_kind"] for candidate in state.candidates] == [
        "incubation",
        "default",
    ]
    assert state.candidates[0]["task_id"] == "task-incubating"
    assert state.candidates[1]["task_id"] is None
