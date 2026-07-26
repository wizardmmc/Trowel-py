from __future__ import annotations

from datetime import datetime, timezone

from trowel_py.model_os.store import ModelOsStore
from trowel_py.model_os.types import SnapshotRef
from trowel_py.model_os.work_broker import BudgetDimensions
from trowel_py.model_os.waking import WakeConditionKind

from tests.model_os._episode_helpers import (
    activate_episode,
    make_cooperative_snapshot,
    make_running_task_episode,
)


NOW = datetime(2026, 7, 26, 8, 0, tzinfo=timezone.utc)


def prepared_running_task(
    store: ModelOsStore, *, key: str = "incubation-prepared"
):
    episode, lease, task, _ = make_running_task_episode(
        store,
        idempotency_key=key,
        goal="设计可恢复的委派控制面",
    )
    activate_episode(store, episode.episode_id, lease)
    store.request_yield(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        reason="保存孵化准备现场",
    )
    snapshot = make_cooperative_snapshot(
        current_judgment="进程内句柄可用，但重启后会丢失",
        unknowns=("重启后怎样确认 child 是否已经执行",),
        next_steps=("设计 durable correlation",),
    )
    ref = store.commit_checkpoint(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
        snapshot=snapshot,
        checkpoint_key=f"checkpoint-{key}",
    )
    store.close_episode(
        episode.episode_id,
        expected_lease_id=lease.lease_id,
        expected_owner=lease.owner,
        expected_token=lease.fencing_token,
    )
    return task, ref


def create_command(task_id: str, ref: SnapshotRef, *, command_id: str = "incubate-1"):
    from trowel_py.model_os.incubation import (
        CreateIncubationPlanCommand,
        IncubationWakeCondition,
    )

    return CreateIncubationPlanCommand(
        command_id=command_id,
        task_id=task_id,
        prepared_snapshot_ref=ref,
        unresolved_question="重启后怎样确认 child 是否已经执行？",
        wake_condition=IncubationWakeCondition(
            kind=WakeConditionKind.TIME,
            target_ref="clock",
            due_at="2026-07-27T08:00:00+00:00",
            match_params={},
        ),
        deadline="2026-07-28T08:00:00+00:00",
        budget=BudgetDimensions(calls=1),
        runtime="codex",
        occurred_at=NOW,
    )
