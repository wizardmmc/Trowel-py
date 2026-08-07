"""验证同步轮次的共同输入、物理分批和原子可见性。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

from trowel_py.agent_host.binding import Runtime
from trowel_py.discussion.artifacts import DiscussionArtifactStore
from trowel_py.discussion.coordinator import DiscussionCoordinator
from trowel_py.discussion.events import DiscussionEventBus
from trowel_py.discussion.episode import DiscussionEpisodeWriter
from trowel_py.discussion.repository import open_discussion_repository
from trowel_py.discussion.schemas import (
    ContinueDiscussionRequest,
    CreateDiscussionRequest,
    VersionedCommand,
)
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


def _inline_request() -> CreateDiscussionRequest:
    """创建不依赖设置页命名会话配置的逐轮研讨请求。"""

    return CreateDiscussionRequest(
        request_id="create-inline",
        topic="直接配置能否与 Agent 保持一致？",
        workdir="/tmp",
        progression_mode="user_guided",
        participants=[
            {
                "name": "claude",
                "connection_id": "cc-main",
                "model": "sonnet",
                "effort": None,
            },
            {
                "name": "codex",
                "connection_id": "codex-main",
                "model": "gpt-test",
                "effort": "high",
            },
        ],
    )


class _NaturalEofStream:
    """模拟只有读到自然 EOF 才释放下一轮发送权的真实会话流。"""

    def __init__(
        self,
        events: list[dict[str, Any]],
        release: Callable[[], None],
    ) -> None:
        """保存事件和自然 EOF 才调用的释放函数。"""

        self._events = iter(events)
        self._release = release

    def __aiter__(self) -> _NaturalEofStream:
        """返回当前异步迭代器。"""

        return self

    async def __anext__(self) -> dict[str, Any]:
        """逐项返回事件，并仅在真实耗尽时释放会话。"""

        try:
            return next(self._events)
        except StopIteration:
            self._release()
            raise StopAsyncIteration from None

    async def aclose(self) -> None:
        """模拟 SSE 消费者提前关流时后台仍在 drain。"""


class NaturalEofSessions(FakeParticipantSessions):
    """复现 finished 后立刻断流会让下一轮收到 turn_in_progress 的运行时。"""

    def __init__(self) -> None:
        super().__init__()
        self.busy: set[str] = set()

    def run_turn(
        self,
        agent_session_id: str,
        prompt: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """正常轮在 finished 后还有尾帧；未释放时返回根 turn 前错误。"""

        calls = self.prompts.setdefault(agent_session_id, [])
        calls.append(prompt)
        round_number = len(calls)
        binding = self._bindings[agent_session_id]
        thread_id = (
            binding.native_session_id
            if binding.runtime is Runtime.CODEX
            else None
        )
        if agent_session_id in self.busy:
            return _NaturalEofStream(
                [
                    {
                        "schema": "agent-event-v1",
                        "session_id": agent_session_id,
                        "runtime": binding.runtime.value,
                        "seq": round_number * 10,
                        "type": "error",
                        "turn_id": None,
                        "thread_id": thread_id,
                        "payload": {"subclass": "turn_in_progress"},
                    }
                ],
                lambda: None,
            )
        self.busy.add(agent_session_id)
        turn_id = f"turn-{agent_session_id}-{round_number}"
        events = [
            {
                "schema": "agent-event-v1",
                "session_id": agent_session_id,
                "runtime": binding.runtime.value,
                "seq": round_number * 10 + 1,
                "type": "turn_start",
                "turn_id": turn_id,
                "thread_id": thread_id,
                "payload": {},
            },
            {
                "schema": "agent-event-v1",
                "session_id": agent_session_id,
                "runtime": binding.runtime.value,
                "seq": round_number * 10 + 2,
                "type": "tool_call",
                "turn_id": turn_id,
                "thread_id": thread_id,
                "item_id": f"tool-{round_number}",
                "payload": {"tool_name": "Read"},
            },
            {
                "schema": "agent-event-v1",
                "session_id": agent_session_id,
                "runtime": binding.runtime.value,
                "seq": round_number * 10 + 3,
                "type": "text",
                "turn_id": turn_id,
                "thread_id": thread_id,
                "payload": {"text": f"answer-{round_number}"},
            },
            {
                "schema": "agent-event-v1",
                "session_id": agent_session_id,
                "runtime": binding.runtime.value,
                "seq": round_number * 10 + 4,
                "type": "finished",
                "turn_id": turn_id,
                "thread_id": thread_id,
                "payload": {},
            },
            {
                "schema": "agent-event-v1",
                "session_id": agent_session_id,
                "runtime": binding.runtime.value,
                "seq": round_number * 10 + 5,
                "type": "host_status",
                "turn_id": None,
                "thread_id": thread_id,
                "payload": {"status": "ready"},
            },
        ]
        return _NaturalEofStream(
            events,
            lambda: self.busy.discard(agent_session_id),
        )


@pytest.mark.anyio
async def test_create_accepts_agent_inline_connection_model_and_effort(
    tmp_path: Path,
) -> None:
    """Agent 可直接启动的组合无需另存命名配置也能冻结为参与者。"""

    service, _ = _build_system(
        tmp_path,
        FakeParticipantSessions(),
        running_limit=2,
    )

    created = await service.create(_inline_request())

    assert [item["runtime"] for item in created["participants"]] == [
        "claude_code",
        "codex",
    ]
    assert [item["model"] for item in created["participants"]] == [
        "sonnet",
        "gpt-test",
    ]
    assert [item["connection_name"] for item in created["participants"]] == [
        "cc-main",
        "codex-main",
    ]
    assert [item["effective_model"] for item in created["participants"]] == [
        "glm-sonnet-test",
        "gpt-test",
    ]


@pytest.mark.anyio
async def test_finished_stream_is_drained_before_automatic_next_round(
    tmp_path: Path,
) -> None:
    """根 finished 不是可提前关流的信号，下一轮必须等自然 EOF 释放会话。"""

    sessions = NaturalEofSessions()
    service, coordinator = _build_system(tmp_path, sessions, running_limit=2)
    created = await service.create(_inline_request())
    service.start(
        created["id"],
        VersionedCommand(command_id="start-drain", expected_version=created["version"]),
    )
    await asyncio.wait_for(coordinator.wait_idle(created["id"]), timeout=2)
    first = service.get(created["id"])
    service.continue_round(
        created["id"],
        ContinueDiscussionRequest(
            command_id="continue-drain",
            expected_version=first["version"],
            progression_mode="automatic",
            additional_rounds=1,
        ),
    )
    await asyncio.wait_for(coordinator.wait_idle(created["id"]), timeout=2)

    published = service.get(created["id"])

    assert [round_["status"] for round_ in published["rounds"]] == [
        "published",
        "published",
    ]
    assert all(
        slot["status"] == "succeeded"
        for round_ in published["rounds"]
        for slot in round_["participants"]
    )
    assert published["rounds"][0]["participants"][0]["activity"] == {
        "tool_call_count": 1,
        "tool_names": {"Read": 1},
        "subagent_count": 0,
    }


@pytest.mark.anyio
async def test_progression_mode_can_alternate_at_published_round_boundaries(
    tmp_path: Path,
) -> None:
    """逐轮、自动批次、再逐轮可以在共同公开边界依次切换。"""

    sessions = FakeParticipantSessions()
    service, coordinator = _build_system(tmp_path, sessions, running_limit=4)
    created = await service.create(_inline_request())
    service.start(
        created["id"],
        VersionedCommand(
            command_id="start-alternating",
            expected_version=created["version"],
        ),
    )
    await asyncio.wait_for(coordinator.wait_idle(created["id"]), timeout=2)
    first = service.get(created["id"])

    service.continue_round(
        created["id"],
        ContinueDiscussionRequest(
            command_id="automatic-two",
            expected_version=first["version"],
            progression_mode="automatic",
            additional_rounds=2,
        ),
    )
    await asyncio.wait_for(coordinator.wait_idle(created["id"]), timeout=2)
    automatic = service.get(created["id"])

    assert automatic["status"] == "waiting_user"
    assert automatic["progression_mode"] == "automatic"
    assert automatic["max_rounds"] == 3
    assert [item["number"] for item in automatic["rounds"]] == [1, 2, 3]

    service.continue_round(
        created["id"],
        ContinueDiscussionRequest(
            command_id="guided-again",
            expected_version=automatic["version"],
            progression_mode="user_guided",
        ),
    )
    await asyncio.wait_for(coordinator.wait_idle(created["id"]), timeout=2)
    guided = service.get(created["id"])

    assert guided["status"] == "waiting_user"
    assert guided["progression_mode"] == "user_guided"
    assert guided["max_rounds"] is None
    assert [item["number"] for item in guided["rounds"]] == [1, 2, 3, 4]


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
