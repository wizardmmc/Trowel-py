"""Model OS Store 的 reducer state 到公开值对象投影。"""

from __future__ import annotations

from collections.abc import Callable

from trowel_py.model_os.reducer import EpisodeState, TaskState
from trowel_py.model_os.types import Episode, Task


def project_task_state(
    state: TaskState,
    *,
    task_type: Callable[..., Task],
) -> Task:
    return task_type(
        task_id=state.task_id,
        origin=state.origin,
        original_goal=state.original_goal,
        appended_constraints=state.appended_constraints,
        status=state.status,
        priority=state.priority,
        warm=state.warm,
        warm_rank=state.warm_rank,
        authorization_scope=state.authorization_scope,
        waiting_condition=state.waiting_condition,
        completion_evidence=state.completion_evidence,
        error_record=state.error_record,
        primary_work_item_id=state.primary_work_item_id,
        created_at=state.created_at,
        updated_at=state.updated_at,
    )


def project_episode_state(
    state: EpisodeState,
    ownership_lease_id: str | None = None,
    *,
    episode_type: Callable[..., Episode],
) -> Episode:
    return episode_type(
        episode_id=state.episode_id,
        work_item_id=state.work_item_id,
        task_id=state.task_id,
        status=state.status,
        native_session_id=state.native_session_id,
        ownership_lease_id=ownership_lease_id,
        last_snapshot_ref=state.last_snapshot_ref,
        pending_descriptor=state.pending_descriptor,
        reconcile_reason=state.reconcile_reason,
        created_at=state.created_at,
        updated_at=state.updated_at,
    )
