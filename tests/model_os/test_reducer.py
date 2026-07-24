"""直接验证 reducer 的来源门禁、前向兼容、幂等和确定性。"""

from __future__ import annotations

from trowel_py.model_os.reducer import initial_snapshot, reduce_event
from trowel_py.model_os.types import (
    EventEnvelope,
    EventKind,
    MemoryEligibility,
    Provenance,
    SessionPurpose,
    WorkItemKind,
    WorkItemStatus,
)


def _created_event(
    *,
    event_id: str,
    kind: WorkItemKind = WorkItemKind.TASK,
    task_id: str | None = "task-A",
    owner_ref: str = "user",
    provenance: Provenance = Provenance.USER_DECISION,
) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        kind=EventKind.WORK_ITEM_CREATED,
        occurred_at="2026-07-21T00:00:00Z",
        source="test",
        provenance=provenance,
        policy_version="v0",
        payload={
            "work_item_id": "wi-1",
            "kind": kind.value,
            "owner_ref": owner_ref,
            "task_id": task_id,
            "status": WorkItemStatus.PENDING.value,
            "session_purpose": SessionPurpose.FOREGROUND.value,
            "memory_eligibility": MemoryEligibility.ELIGIBLE.value,
        },
        work_item_id="wi-1",
    )


def _status_event(
    *,
    event_id: str,
    work_item_id: str,
    new_status: WorkItemStatus,
    provenance: Provenance,
) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        kind=EventKind.WORK_ITEM_STATUS_CHANGED,
        occurred_at="2026-07-21T00:00:01Z",
        source="test",
        provenance=provenance,
        policy_version="v0",
        payload={"new_status": new_status.value},
        work_item_id=work_item_id,
    )


def test_created_event_produces_work_item_state() -> None:
    snap = reduce_event(initial_snapshot(), _created_event(event_id="e1"))
    assert len(snap.work_items) == 1
    state = snap.work_items[0]
    assert state.work_item_id == "wi-1"
    assert state.kind == WorkItemKind.TASK
    assert state.status == WorkItemStatus.PENDING
    # 创建只证明对象存在，状态来源保持最弱占位，等待真实观测。
    assert state.status_provenance == Provenance.STALE


def test_machine_observation_status_change_applies() -> None:
    snap = reduce_event(initial_snapshot(), _created_event(event_id="e1"))
    snap = reduce_event(
        snap,
        _status_event(
            event_id="e2",
            work_item_id="wi-1",
            new_status=WorkItemStatus.RUNNING,
            provenance=Provenance.MACHINE_OBSERVATION,
        ),
    )
    assert snap.work_items[0].status == WorkItemStatus.RUNNING


def test_model_hypothesis_cannot_override_machine_observation() -> None:
    snap = reduce_event(initial_snapshot(), _created_event(event_id="e1"))
    snap = reduce_event(
        snap,
        _status_event(
            event_id="e2",
            work_item_id="wi-1",
            new_status=WorkItemStatus.RUNNING,
            provenance=Provenance.MACHINE_OBSERVATION,
        ),
    )
    snap = reduce_event(
        snap,
        _status_event(
            event_id="e3",
            work_item_id="wi-1",
            new_status=WorkItemStatus.DONE,
            provenance=Provenance.MODEL_HYPOTHESIS,
        ),
    )
    # 较弱来源不能改变派生状态。
    assert snap.work_items[0].status == WorkItemStatus.RUNNING
    assert snap.work_items[0].status_provenance == Provenance.MACHINE_OBSERVATION


def test_user_decision_overrides_model_hypothesis() -> None:
    snap = reduce_event(initial_snapshot(), _created_event(event_id="e1"))
    snap = reduce_event(
        snap,
        _status_event(
            event_id="e2",
            work_item_id="wi-1",
            new_status=WorkItemStatus.DONE,
            provenance=Provenance.MODEL_HYPOTHESIS,
        ),
    )
    snap = reduce_event(
        snap,
        _status_event(
            event_id="e3",
            work_item_id="wi-1",
            new_status=WorkItemStatus.RUNNING,
            provenance=Provenance.USER_DECISION,
        ),
    )
    assert snap.work_items[0].status == WorkItemStatus.RUNNING
    assert snap.work_items[0].status_provenance == Provenance.USER_DECISION


def test_unknown_cannot_assert_anything() -> None:
    snap = reduce_event(initial_snapshot(), _created_event(event_id="e1"))
    snap = reduce_event(
        snap,
        _status_event(
            event_id="e2",
            work_item_id="wi-1",
            new_status=WorkItemStatus.RUNNING,
            provenance=Provenance.MACHINE_OBSERVATION,
        ),
    )
    snap = reduce_event(
        snap,
        _status_event(
            event_id="e3",
            work_item_id="wi-1",
            new_status=WorkItemStatus.DONE,
            provenance=Provenance.UNKNOWN,
        ),
    )
    assert snap.work_items[0].status == WorkItemStatus.RUNNING


def test_unknown_event_kind_does_not_crash_and_is_retained() -> None:
    snap = initial_snapshot()
    unknown = EventEnvelope(
        event_id="e-future",
        kind="some.future.kind.v2",  # 当前 reducer 版本未知的事件
        occurred_at="2026-07-21T00:00:00Z",
        source="future",
        provenance=Provenance.MACHINE_OBSERVATION,
        policy_version="v0",
        payload={"whatever": 1},
    )
    snap = reduce_event(snap, unknown)
    assert "some.future.kind.v2" in snap.unrecognized_event_kinds
    # reducer 未崩溃，且仍返回有效快照。
    assert snap.last_seq >= 0


def test_duplicate_created_event_is_idempotent() -> None:
    snap = reduce_event(initial_snapshot(), _created_event(event_id="e1"))
    snap = reduce_event(snap, _created_event(event_id="e1-again"))
    assert len(snap.work_items) == 1


def test_fold_is_deterministic_across_runs() -> None:
    events = [
        _created_event(event_id="e1"),
        _status_event(
            event_id="e2",
            work_item_id="wi-1",
            new_status=WorkItemStatus.RUNNING,
            provenance=Provenance.MACHINE_OBSERVATION,
        ),
    ]

    def fold_all() -> object:
        snap = initial_snapshot()
        for ev in events:
            snap = reduce_event(snap, ev)
        return snap

    assert fold_all() == fold_all()
