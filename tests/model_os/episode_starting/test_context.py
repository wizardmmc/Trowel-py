from __future__ import annotations

import pytest

from tests.model_os._episode_helpers import (
    make_cooperative_snapshot,
    make_running_task_episode,
)
from trowel_py.model_os.episode_starting import (
    StartEpisodeCommand,
    build_episode_context,
)
from trowel_py.model_os.types import MemoryEligibility, SessionPurpose


def _command(work_item_id: str, task_id: str, **overrides) -> StartEpisodeCommand:
    values = {
        "work_item_id": work_item_id,
        "task_id": task_id,
        "previous_episode_id": None,
        "previous_snapshot_ref": None,
        "runtime": "codex",
        "model": "deep-model",
        "effort": "high",
        "memory_enabled": True,
        "profile_enabled": False,
        "workdir": "/workspace/project",
        "session_purpose": SessionPurpose.FOREGROUND,
        "memory_eligibility": MemoryEligibility.ELIGIBLE,
        "permission": "workspace-write",
        "idempotency_key": "start-1",
    }
    values.update(overrides)
    return StartEpisodeCommand(**values)


def test_first_episode_context_uses_immutable_goal_and_appended_decisions(store) -> None:
    episode, _, task, work_item_id = make_running_task_episode(store)
    store.append_constraint(task.task_id, "只修改后端")
    command = _command(work_item_id, task.task_id)

    context = build_episode_context(store, command, episode_id=episode.episode_id)
    rendered = context.render()

    assert context.original_goal == "调研一个缓存失效检测方案"
    assert context.user_decisions == ("只修改后端",)
    assert context.previous_snapshot is None
    assert "snapshot 可能有错" in rendered
    assert "先核查当前事实和工具结果" in rendered
    assert "/workspace/project" not in rendered


def test_fresh_context_requires_a_committed_snapshot(store) -> None:
    episode, lease, task, work_item_id = make_running_task_episode(store)
    snapshot = make_cooperative_snapshot()
    with pytest.raises(ValueError, match="previous_snapshot_ref"):
        _command(
            work_item_id,
            task.task_id,
            previous_episode_id=episode.episode_id,
            previous_snapshot_ref=snapshot,
        )


def test_command_rejects_resume_and_mismatched_mp_policy(store) -> None:
    _, _, task, work_item_id = make_running_task_episode(store)
    with pytest.raises(ValueError, match="resume"):
        _command(work_item_id, task.task_id, resume_from="native-old")
    with pytest.raises(ValueError, match="session_purpose"):
        build_episode_context(
            store,
            _command(
                work_item_id,
                task.task_id,
                session_purpose=SessionPurpose.DEFAULT,
            ),
            episode_id="episode-new",
        )
