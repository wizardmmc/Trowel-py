"""验证用户标记与普通 Agent 交接的确定性现场。"""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.discussion.support import FakeConfigurationCatalog, FakeParticipantSessions
from trowel_py.agent_host.schemas import CreateAgentSessionRequest
from trowel_py.discussion.artifacts import DiscussionArtifactStore
from trowel_py.discussion.coordinator import DiscussionCoordinator
from trowel_py.discussion.episode import DiscussionEpisodeWriter
from trowel_py.discussion.events import DiscussionEventBus
from trowel_py.discussion.handoff import (
    AgentHostHandoffSessionAdapter,
    HandoffSessionResult,
)
from trowel_py.discussion.repository import open_discussion_repository
from trowel_py.discussion.schemas import (
    CreateDiscussionHandoffRequest,
    CreateDiscussionRequest,
    MarkDiscussionResultRequest,
    VersionedCommand,
)
from trowel_py.discussion.service import DiscussionService


class FakeHandoffSessions:
    """捕获普通 Agent 创建请求和交接提示词。"""

    def __init__(self) -> None:
        """创建尚未收到交接请求的测试端口。"""

        self.calls: list[tuple[str, CreateAgentSessionRequest, str, str]] = []

    async def create_and_start(
        self,
        *,
        request_id: str,
        request: CreateAgentSessionRequest,
        prompt: str,
        instruction: str,
    ) -> HandoffSessionResult:
        """记录一次交接并返回稳定的普通会话身份。"""

        self.calls.append((request_id, request, prompt, instruction))
        return HandoffSessionResult(
            agent_session_id="agent-handoff-1",
            turn_id="turn-handoff-1",
        )


class _OwnerStore:
    """模拟可跨进程恢复 owner_ref 的 binding store。"""

    def __init__(self) -> None:
        self.binding = None

    def find_by_owner_ref(self, _owner_ref: str):
        """返回此前持久化的普通 Agent binding。"""

        return self.binding


class _HandoffHub:
    """只实现交接 adapter 用到的 Session Hub 边界。"""

    def __init__(self) -> None:
        self.store = _OwnerStore()
        self.create_calls: list[str] = []
        self.turn_calls: list[tuple[str, bool, bool]] = []
        self.current_turn_id: str | None = None

    async def create_complete_session(self, request, *, bootstrap_context=None):
        """持久化 owner_ref，并捕获系统级交接背景。"""

        self.create_calls.append(bootstrap_context)
        binding = SimpleNamespace(
            session_id="stable-agent",
            native_session_id=None,
        )
        self.store.binding = binding
        return binding

    async def coalesce_session_create(
        self, _request_id: str, _fingerprint: str, operation
    ):
        """执行首次创建；测试不模拟同进程竞争。"""

        return await operation()

    def list_active(self):
        """公开当前已接受的首轮身份。"""

        return (
            [
                {
                    "session_id": "stable-agent",
                    "current_turn_id": self.current_turn_id,
                    "connected": True,
                }
            ],
            None,
        )

    async def start_turn(
        self,
        _session_id: str,
        text: str,
        *,
        autonomous: bool,
        memory_eligible: bool,
        reserved_turn_id: str | None = None,
    ) -> str:
        """捕获首轮来源资格并返回稳定 turn。"""

        self.turn_calls.append((text, autonomous, memory_eligible))
        self.current_turn_id = reserved_turn_id or "stable-turn"
        return self.current_turn_id

    async def history(self, _session_id: str):
        """首次启动前没有原生历史。"""

        return []

    async def delete(self, _session_id: str) -> bool:
        """删除测试 binding，供真正重启恢复测试使用。"""

        self.store.binding = None
        return True

    def activate(self, _session_id: str) -> None:
        """接受普通会话激活。"""


class _RestartedHandoffHub(_HandoffHub):
    """模拟只有持久 binding 和原生历史、没有旧 runtime 的新进程。"""

    def __init__(self) -> None:
        super().__init__()
        self.store.binding = SimpleNamespace(
            session_id="stale-agent",
            native_session_id="native-thread",
        )
        self.deleted: list[str] = []

    def list_active(self):
        """新 Hub 只能看到断开的旧 binding 或新建的恢复 binding。"""

        binding = self.store.binding
        if binding is None:
            return [], None
        return [
            {
                "session_id": binding.session_id,
                "current_turn_id": None,
                "connected": binding.session_id != "stale-agent",
            }
        ], None

    async def create_complete_session(self, request, *, bootstrap_context=None):
        """用旧原生身份创建可工作的恢复 binding。"""

        assert request.resume_from == "native-thread"
        self.create_calls.append(bootstrap_context)
        binding = SimpleNamespace(
            session_id="recovered-agent",
            native_session_id=request.resume_from,
        )
        self.store.binding = binding
        return binding

    async def history(self, session_id: str):
        """模拟没有稳定 turn ID 的 Claude Code 原生历史。"""

        if session_id == "recovered-agent":
            return [{"type": "text", "text": "已完成交接"}]
        return []

    async def delete(self, session_id: str) -> bool:
        """清理失去 runtime 的旧 Trowel binding，不删除原生 thread。"""

        self.deleted.append(session_id)
        self.store.binding = None
        return True


@pytest.mark.anyio
async def test_handoff_context_is_system_owned_and_retry_reuses_binding() -> None:
    """交接现场不冒充用户原话，同进程重试也不创建第二个 Agent。"""

    hub = _HandoffHub()
    request = CreateAgentSessionRequest(
        runtime="codex",
        workdir="/tmp",
        session_kind="user",
        owner_ref="discussion:d1:handoff:c1:v1",
        agent_mcp_enabled=False,
    )
    first = await AgentHostHandoffSessionAdapter(hub).create_and_start(
        request_id="handoff:d1:c1",
        request=request,
        prompt="含参与者立场的交接现场",
        instruction="根据现场实现下一步",
    )
    second = await AgentHostHandoffSessionAdapter(hub).create_and_start(
        request_id="handoff:d1:c1",
        request=request,
        prompt="含参与者立场的交接现场",
        instruction="根据现场实现下一步",
    )

    assert first == second == HandoffSessionResult("stable-agent", "stable-turn")
    assert hub.create_calls == ["含参与者立场的交接现场"]
    assert hub.turn_calls == [("根据现场实现下一步", False, False)]


@pytest.mark.anyio
async def test_process_restart_resumes_native_handoff_without_second_turn() -> None:
    """Claude Code 重启恢复时用确定性逻辑 ID 对账没有 turn ID 的历史。"""

    hub = _RestartedHandoffHub()
    request = CreateAgentSessionRequest(
        runtime="claude_code",
        workdir="/tmp",
        session_kind="user",
        owner_ref="discussion:d1:handoff:c1:v1",
        memory_eligibility=False,
        agent_mcp_enabled=False,
    )

    result = await AgentHostHandoffSessionAdapter(hub).create_and_start(
        request_id="handoff:d1:c1",
        request=request,
        prompt="系统交接现场",
        instruction="继续做实现",
    )

    assert hub.deleted == ["stale-agent"]
    assert hub.create_calls == ["系统交接现场"]
    assert hub.turn_calls == []
    logical_turn_id = hashlib.sha256(
        b"handoff:d1:c1:bootstrap-turn"
    ).hexdigest()[:32]
    assert result == HandoffSessionResult("recovered-agent", logical_turn_id)


def _build_system(
    tmp_path: Path,
) -> tuple[DiscussionService, DiscussionCoordinator, FakeHandoffSessions]:
    """装配真实仓储、artifact 与可观察交接端口。"""

    db_path = tmp_path / "trowel.db"

    def opener():
        """打开当前测试的隔离 discussion 仓储。"""

        return open_discussion_repository(db_path)

    artifacts = DiscussionArtifactStore(tmp_path / "data")
    events = DiscussionEventBus()
    participant_sessions = FakeParticipantSessions()
    handoff_sessions = FakeHandoffSessions()
    coordinator = DiscussionCoordinator(
        opener,
        artifacts,
        participant_sessions,
        events,
        DiscussionEpisodeWriter(artifacts, memory_root=tmp_path / "memory"),
    )
    service = DiscussionService(
        opener,
        artifacts,
        coordinator,
        events,
        FakeConfigurationCatalog(),
        handoff_sessions=handoff_sessions,
        transcript_access_token_factory=lambda _path: "scoped-token",
    )
    return service, coordinator, handoff_sessions


async def _published_discussion(
    service: DiscussionService,
    coordinator: DiscussionCoordinator,
) -> dict[str, object]:
    """创建并完成一轮可供标记和交接的研讨。"""

    created = await service.create(
        CreateDiscussionRequest(
            request_id="handoff-source",
            topic="比较两种 Agent 状态管理方案",
            workdir="/tmp",
            progression_mode="user_guided",
            participants=[
                {
                    "name": "glm",
                    "session_configuration_id": "cc-glm",
                    "memory_enabled": True,
                    "profile_enabled": False,
                    "self_enabled": True,
                },
                {
                    "name": "gpt",
                    "session_configuration_id": "codex-gpt",
                    "memory_enabled": False,
                    "profile_enabled": True,
                    "self_enabled": False,
                },
            ],
        )
    )
    service.start(
        str(created["id"]),
        VersionedCommand(
            command_id="handoff-start",
            expected_version=int(created["version"]),
        ),
    )
    await asyncio.wait_for(coordinator.wait_idle(str(created["id"])), timeout=2)
    return service.get(str(created["id"]))


@pytest.mark.anyio
async def test_marked_result_is_visible_and_enters_deterministic_handoff(
    tmp_path: Path,
) -> None:
    """交接只拼装已有事实，并创建启用独立上下文开关的普通 Agent。"""

    service, coordinator, handoff_sessions = _build_system(tmp_path)
    waiting = await _published_discussion(service, coordinator)
    participant_id = str(waiting["participants"][0]["id"])
    marked = service.mark_result(
        str(waiting["id"]),
        MarkDiscussionResultRequest(
            command_id="mark-glm",
            expected_version=int(waiting["version"]),
            round_number=1,
            participant_id=participant_id,
            marked=True,
        ),
    )

    result = await service.handoff(
        str(marked["id"]),
        CreateDiscussionHandoffRequest(
            command_id="handoff-agent",
            expected_version=int(marked["version"]),
            instruction="根据研讨结论整理实现计划",
            agent={
                "runtime": "codex",
                "connection_id": "connection-codex-gpt",
                "workdir": "/tmp",
                "model": "model-test",
                "effort": "high",
                "permission_preset": "workspace-write",
                "memory_enabled": True,
                "profile_enabled": False,
                "self_enabled": True,
            },
        ),
    )

    assert marked["rounds"][0]["participants"][0]["marked"] is True
    assert result["agent_session_id"] == "agent-handoff-1"
    assert result["turn_id"] == "turn-handoff-1"
    assert len(handoff_sessions.calls) == 1
    request_id, request, prompt, instruction = handoff_sessions.calls[0]
    assert request_id.startswith("discussion-handoff:")
    assert request.session_kind == "user"
    assert request.memory_enabled is True
    assert request.profile_enabled is False
    assert request.self_enabled is True
    assert request.agent_mcp_enabled is False
    assert request.memory_eligibility is False
    assert request.owner_ref == (
        f"discussion:{marked['id']}:handoff:handoff-agent:v1"
    )
    assert instruction == "根据研讨结论整理实现计划"
    assert "比较两种 Agent 状态管理方案" in prompt
    assert "answer-glm-round-1" in prompt
    assert "用户标记" in prompt
    assert "/api/discussions/" in prompt
    assert "/transcript" in prompt
    assert "access_token=scoped-token" in prompt
    assert str(tmp_path / "data") not in prompt
    assert "共识摘要" not in prompt

    repeated = await service.handoff(
        str(marked["id"]),
        CreateDiscussionHandoffRequest(
            command_id="handoff-agent",
            expected_version=int(marked["version"]),
            instruction="根据研讨结论整理实现计划",
            agent={
                "runtime": "codex",
                "connection_id": "connection-codex-gpt",
                "workdir": "/tmp",
                "model": "model-test",
                "effort": "high",
                "permission_preset": "workspace-write",
                "memory_enabled": True,
                "profile_enabled": False,
                "self_enabled": True,
            },
        ),
    )
    assert repeated["agent_session_id"] == "agent-handoff-1"
    assert len(handoff_sessions.calls) == 1


@pytest.mark.anyio
async def test_unmark_removes_result_from_public_state(tmp_path: Path) -> None:
    """取消标记必须按 versioned command 持久生效。"""

    service, coordinator, _handoff_sessions = _build_system(tmp_path)
    waiting = await _published_discussion(service, coordinator)
    participant_id = str(waiting["participants"][0]["id"])
    marked = service.mark_result(
        str(waiting["id"]),
        MarkDiscussionResultRequest(
            command_id="mark-on",
            expected_version=int(waiting["version"]),
            round_number=1,
            participant_id=participant_id,
            marked=True,
        ),
    )
    unmarked = service.mark_result(
        str(waiting["id"]),
        MarkDiscussionResultRequest(
            command_id="mark-off",
            expected_version=int(marked["version"]),
            round_number=1,
            participant_id=participant_id,
            marked=False,
        ),
    )

    assert unmarked["rounds"][0]["participants"][0]["marked"] is False
