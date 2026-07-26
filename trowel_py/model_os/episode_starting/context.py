"""从 Store 与 runtime 事实组装首轮 Episode Context。"""

from __future__ import annotations

from trowel_py.model_os.episode_starting.models import (
    EpisodeContext,
    StartEpisodeCommand,
)
from trowel_py.model_os.self_assembler import (
    build_self_manifest,
    render_self_injection,
)
from trowel_py.model_os.store import ModelOsStore


def build_episode_context(
    store: ModelOsStore,
    command: StartEpisodeCommand,
    *,
    episode_id: str,
    native_session_id: str | None = None,
) -> EpisodeContext:
    snapshot = store.read_snapshot()
    work_item = next(
        (
            item
            for item in snapshot.work_items
            if item.work_item_id == command.work_item_id
        ),
        None,
    )
    if work_item is None:
        raise ValueError(f"unknown work_item_id={command.work_item_id!r}")
    if work_item.task_id != command.task_id:
        raise ValueError("task_id does not match WorkItem")
    if work_item.session_purpose is not command.session_purpose:
        raise ValueError("session_purpose does not match WorkItem policy")
    if work_item.memory_eligibility is not command.memory_eligibility:
        raise ValueError("memory_eligibility does not match WorkItem policy")

    task = None
    if command.task_id is not None:
        task = next(
            (item for item in snapshot.tasks if item.task_id == command.task_id), None
        )
        if task is None:
            raise ValueError(f"unknown task_id={command.task_id!r}")
    previous = (
        store.read_episode_snapshot(command.previous_snapshot_ref)
        if command.previous_snapshot_ref is not None
        else None
    )
    original_goal = task.original_goal if task is not None else work_item.owner_ref
    decisions = task.appended_constraints if task is not None else ()
    manifest = build_self_manifest(
        runtime=command.runtime,
        model=command.model,
        effort=command.effort,
        memory_enabled=command.memory_enabled,
        profile_enabled=command.profile_enabled,
        permission_preset=command.permission,
        task_id=command.task_id,
        episode_id=episode_id,
        native_session_id=native_session_id,
    )
    return EpisodeContext(
        self_text=render_self_injection(manifest),
        work_item_id=command.work_item_id,
        task_id=command.task_id,
        episode_id=episode_id,
        original_goal=original_goal,
        user_decisions=tuple(decisions),
        previous_snapshot=previous,
        memory_enabled=command.memory_enabled,
        profile_enabled=command.profile_enabled,
    )
