"""验证同步轮次的共同输入、物理分批和原子可见性。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from trowel_py.discussion.artifacts import DiscussionArtifactStore
from trowel_py.discussion.coordinator import DiscussionCoordinator
from trowel_py.discussion.events import DiscussionEventBus
from trowel_py.discussion.episode import DiscussionEpisodeWriter
from trowel_py.discussion.repository import open_discussion_repository
from trowel_py.discussion.schemas import CreateDiscussionRequest, VersionedCommand
from trowel_py.discussion.service import DiscussionService
from tests.discussion.support import (
    FakeConfigurationCatalog,
    FakeParticipantSessions,
)


def _build_system(
    tmp_path: Path,
    sessions: FakeParticipantSessions,
    *,
    running_limit: int,
) -> tuple[DiscussionService, DiscussionCoordinator]:
    """装配使用同一临时主库和 data root 的完整 discussion 后端。

    Args:
        tmp_path: 当前测试隔离目录。
        sessions: 可控 participant 端口。
        running_limit: coordinator 物理并发上限。

    Returns:
        service 与 coordinator。
    """

    db_path = tmp_path / "trowel.db"

    def opener():
        """打开当前测试唯一主库的短连接 repository。"""

        return open_discussion_repository(db_path)

    artifacts = DiscussionArtifactStore(tmp_path / "data")
    events = DiscussionEventBus()
    episode_writer = DiscussionEpisodeWriter(
        artifacts,
        memory_root=tmp_path / "memory",
    )
    coordinator = DiscussionCoordinator(
        opener,
        artifacts,
        sessions,
        events,
        episode_writer,
        max_running_participants=running_limit,
    )
    service = DiscussionService(
        opener,
        artifacts,
        coordinator,
        events,
        FakeConfigurationCatalog(),
    )
    return service, coordinator


def _create_request(*, max_rounds: int = 2) -> CreateDiscussionRequest:
    """创建两种 runtime 混合的四 participant 自动研讨请求。"""

    return CreateDiscussionRequest(
        request_id="create-one",
        topic="是否采用方案 A？",
        workdir="/tmp",
        progression_mode="automatic",
        max_rounds=max_rounds,
        participants=[
            {"name": "glm", "session_configuration_id": "cc-glm"},
            {"name": "gpt", "session_configuration_id": "codex-gpt"},
            {"name": "deepseek", "session_configuration_id": "cc-deepseek"},
            {"name": "luna", "session_configuration_id": "codex-luna"},
        ],
    )


@pytest.mark.anyio
async def test_round_barrier_keeps_one_prompt_for_all_physical_batches(
    tmp_path: Path,
) -> None:
    release = asyncio.Event()
    sessions = FakeParticipantSessions(release=release)
    service, coordinator = _build_system(tmp_path, sessions, running_limit=2)
    created = await service.create(_create_request(max_rounds=1))

    running = service.start(
        created["id"],
        VersionedCommand(command_id="start-one", expected_version=created["version"]),
    )
    while sessions.started_count < 2:
        await asyncio.wait_for(sessions.started.wait(), timeout=0.2)
        sessions.started.clear()

    sealed = service.get(created["id"])
    assert sealed["rounds"][0]["status"] == "running"
    assert all(slot["content"] is None for slot in sealed["rounds"][0]["participants"])
    assert sessions.peak_active == 2

    release.set()
    await asyncio.wait_for(coordinator.wait_idle(created["id"]), timeout=1)
    published = service.get(created["id"])
    prompts = [items[0] for items in sessions.prompts.values()]

    assert len(prompts) == 4
    assert len(set(prompts)) == 1
    assert "answer-" not in prompts[0]
    assert published["rounds"][0]["status"] == "published"
    assert [slot["status"] for slot in published["rounds"][0]["participants"]] == [
        "succeeded",
        "succeeded",
        "succeeded",
        "succeeded",
    ]
    assert running["round_timeout_seconds"] is None
    assert running["budget"] is None


@pytest.mark.anyio
async def test_second_round_reads_all_previous_public_text_without_ai_summary(
    tmp_path: Path,
) -> None:
    sessions = FakeParticipantSessions()
    service, coordinator = _build_system(tmp_path, sessions, running_limit=2)
    created = await service.create(_create_request(max_rounds=2))
    service.start(
        created["id"],
        VersionedCommand(command_id="start-two", expected_version=created["version"]),
    )

    await asyncio.wait_for(coordinator.wait_idle(created["id"]), timeout=2)
    finished = service.get(created["id"])
    second_prompts = [items[1] for items in sessions.prompts.values()]

    assert finished["status"] == "waiting_user"
    assert len(finished["rounds"]) == 2
    assert len(set(second_prompts)) == 1
    for name in ("glm", "gpt", "deepseek", "luna"):
        assert f"answer-{name}-round-1" in second_prompts[0]
    assert "上一轮（第 1 轮）公开发言" in second_prompts[0]
    assert sessions.peak_active <= 2


@pytest.mark.anyio
async def test_eight_participants_complete_two_rounds_with_five_running_limit(
    tmp_path: Path,
) -> None:
    """最大 participant 集合保持完整槽位、共同 prompt 和独立物理并发上限。"""

    sessions = FakeParticipantSessions()
    service, coordinator = _build_system(tmp_path, sessions, running_limit=5)
    request = CreateDiscussionRequest(
        request_id="eight-participants",
        topic="八方讨论",
        workdir="/tmp",
        progression_mode="automatic",
        max_rounds=2,
        participants=[
            {
                "name": f"participant-{index}",
                "session_configuration_id": (
                    f"cc-{index}" if index % 2 == 0 else f"codex-{index}"
                ),
            }
            for index in range(8)
        ],
    )
    created = await service.create(request)
    service.start(
        created["id"],
        VersionedCommand(
            command_id="start-eight",
            expected_version=created["version"],
        ),
    )
    await asyncio.wait_for(coordinator.wait_idle(created["id"]), timeout=3)

    finished = service.get(created["id"])
    assert len(finished["participants"]) == 8
    assert len(finished["rounds"]) == 2
    assert all(
        len(round_record["participants"]) == 8 for round_record in finished["rounds"]
    )
    assert all(len(prompts) == 2 for prompts in sessions.prompts.values())
    assert len({prompts[0] for prompts in sessions.prompts.values()}) == 1
    assert len({prompts[1] for prompts in sessions.prompts.values()}) == 1
    assert sessions.peak_active <= 5
