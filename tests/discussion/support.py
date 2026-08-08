"""提供 discussion 单测共用的临时主库、配置和可控 participant port。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from trowel_py.agent_host.binding import Runtime, SessionBinding, make_binding
from trowel_py.configuration.models import (
    CapabilityView,
    RuntimeKind,
    SessionConfigurationView,
)
from trowel_py.discussion.models import DiscussionParticipant
from trowel_py.discussion.models import ParticipantAttemptHistoryRequest
from trowel_py.discussion.participant_sessions import ParticipantSession
from trowel_py.discussion.service import ParticipantRuntimeIdentity


class FakeConfigurationCatalog:
    """按配置 ID 返回两种 runtime 的已验证会话配置。"""

    def get(self, configuration_id: str) -> SessionConfigurationView:
        """根据 ID 前缀选择 Claude Code 或 Codex 配置。

        Args:
            configuration_id: ``cc-`` 开头使用 Claude Code，其余使用 Codex。

        Returns:
            availability=available 的最小设置域读模型。
        """

        runtime = (
            RuntimeKind.CLAUDE_CODE
            if configuration_id.startswith("cc-")
            else RuntimeKind.CODEX
        )
        return SessionConfigurationView(
            id=configuration_id,
            version=1,
            name=configuration_id,
            runtime=runtime,
            connection_id=f"connection-{configuration_id}",
            connection_identity_version=1,
            model="model-test",
            effort="high",
            capability=CapabilityView(
                status="verified",
                version="test-v1",
                source="真实测试替身",
            ),
            availability="available",
            disabled_reason=None,
        )

    def resolve(
        self,
        connection_id: str,
        *,
        model: str,
        effort: str | None,
    ) -> SessionConfigurationView:
        """模拟 Agent 直接组合经过同一能力校验但不产生命名配置。

        Args:
            connection_id: 测试连接 ID。
            model: 测试模型 ID。
            effort: 测试思考强度。

        Returns:
            id 为空、可直接冻结进 participant 的临时配置。
        """

        runtime = (
            RuntimeKind.CLAUDE_CODE
            if connection_id.startswith("cc-")
            else RuntimeKind.CODEX
        )
        return SessionConfigurationView(
            id="",
            version=0,
            name=connection_id,
            runtime=runtime,
            connection_id=connection_id,
            connection_identity_version=1,
            model=model,
            effort=effort,
            capability=CapabilityView(
                status="verified",
                version="test-v1",
                source="真实测试替身",
            ),
            availability="available",
            disabled_reason=None,
        )

    def runtime_identity(
        self,
        connection_id: str,
        *,
        model: str,
        effort: str | None,
    ) -> ParticipantRuntimeIdentity:
        """返回与真实连接解析同形的冻结展示身份。

        Args:
            connection_id: 测试连接 ID。
            model: 请求模型或角色别名。
            effort: 本测试不参与身份计算的思考强度。

        Returns:
            可用于公开 DTO 断言的连接名和实际模型。
        """

        del effort
        effective_model = {
            "sonnet": "glm-sonnet-test",
            "opus": "deepseek-opus-test",
        }.get(model, model)
        return ParticipantRuntimeIdentity(
            connection_name=connection_id.removeprefix("connection-"),
            effective_model=effective_model,
        )


class FakeParticipantSessions:
    """生成真实 AgentEvent shape，并用 Event 精确控制物理分批窗口。"""

    def __init__(self, *, release: asyncio.Event | None = None) -> None:
        """创建可选 barrier 的 participant port。

        Args:
            release: 存在时，每个 turn 在发出 turn_start 后等待该事件。
        """

        self.release = release
        self.started = asyncio.Event()
        self.started_count = 0
        self.active = 0
        self.peak_active = 0
        self.prompts: dict[str, list[str]] = {}
        self.names: dict[str, str] = {}
        self._bindings: dict[str, SessionBinding] = {}

    async def ensure_session(
        self,
        participant: DiscussionParticipant,
        *,
        workdir: str,
    ) -> ParticipantSession:
        """为 participant 创建确定性测试 binding。

        Args:
            participant: 当前参与者。
            workdir: 冻结工作目录。

        Returns:
            可立即运行的测试会话身份。
        """

        session_id = f"session-{participant.id}"
        native_id = f"native-{participant.id}"
        self.names[session_id] = participant.name
        if session_id not in self._bindings:
            self._bindings[session_id] = make_binding(
                session_id=session_id,
                runtime=participant.runtime,
                native_session_id=native_id,
                workdir=workdir,
                model=participant.model,
                effort=participant.effort,
                permission=(
                    participant.permission_mode
                    if participant.runtime is Runtime.CLAUDE_CODE
                    else participant.permission_preset
                ),
                permission_preset=participant.permission_preset,
                memory_enabled=participant.memory_enabled,
                memory_mcp_enabled=False,
                profile_enabled=participant.profile_enabled,
                self_enabled=participant.self_enabled,
                session_kind="discussion",
                memory_eligibility=False,
                agent_mcp_enabled=False,
                owner_ref=participant.owner_ref,
                capabilities=(),
                name=participant.name,
            )
        return ParticipantSession(
            agent_session_id=session_id,
            native_session_id=native_id,
            capability_version="test-v1",
            capability_source="真实测试替身",
        )

    async def read_attempt_history(
        self,
        request: ParticipantAttemptHistoryRequest,
    ) -> list[dict[str, Any]]:
        """返回已建立统一契约的最小单轮回放。"""

        session_id = request.agent_session_id or f"attempt-{request.id}"
        return [
            {
                "schema": "agent-event-v1",
                "session_id": session_id,
                "runtime": request.runtime.value,
                "seq": 1,
                "type": "user",
                "thread_id": request.native_session_id
                if request.runtime is Runtime.CODEX
                else None,
                "turn_id": request.root_turn_id,
                "item_id": None,
                "payload": {"text": "测试公共输入"},
            },
            {
                "schema": "agent-event-v1",
                "session_id": session_id,
                "runtime": request.runtime.value,
                "seq": 2,
                "type": "text",
                "thread_id": request.native_session_id
                if request.runtime is Runtime.CODEX
                else None,
                "turn_id": request.root_turn_id,
                "item_id": None,
                "payload": {"text": "测试历史回答"},
            },
        ]

    def run_turn(
        self,
        agent_session_id: str,
        prompt: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """返回带当前 root turn 的正常 text+finished 事件流。

        Args:
            agent_session_id: 当前测试会话 ID。
            prompt: 协调器实际发送的公共输入。

        Returns:
            可等待 barrier 的异步事件流。
        """

        async def generate() -> AsyncIterator[dict[str, Any]]:
            """记录并发窗口，并按真实 envelope 字段发出一轮。"""

            calls = self.prompts.setdefault(agent_session_id, [])
            calls.append(prompt)
            round_number = len(calls)
            turn_id = f"turn-{agent_session_id}-{round_number}"
            binding = self._bindings[agent_session_id]
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
            self.started_count += 1
            self.started.set()
            try:
                yield {
                    "schema": "agent-event-v1",
                    "session_id": agent_session_id,
                    "runtime": binding.runtime.value,
                    "seq": round_number * 10 + 1,
                    "type": "turn_start",
                    "turn_id": turn_id,
                    "thread_id": (
                        binding.native_session_id
                        if binding.runtime is Runtime.CODEX
                        else None
                    ),
                    "payload": {},
                }
                if self.release is not None:
                    await self.release.wait()
                answer = f"answer-{self.names[agent_session_id]}-round-{round_number}"
                yield {
                    "schema": "agent-event-v1",
                    "session_id": agent_session_id,
                    "runtime": binding.runtime.value,
                    "seq": round_number * 10 + 2,
                    "type": "text",
                    "turn_id": turn_id,
                    "thread_id": (
                        binding.native_session_id
                        if binding.runtime is Runtime.CODEX
                        else None
                    ),
                    "payload": {"text": answer},
                }
                yield {
                    "schema": "agent-event-v1",
                    "session_id": agent_session_id,
                    "runtime": binding.runtime.value,
                    "seq": round_number * 10 + 3,
                    "type": "finished",
                    "turn_id": turn_id,
                    "thread_id": (
                        binding.native_session_id
                        if binding.runtime is Runtime.CODEX
                        else None
                    ),
                    "payload": {"duration_ms": 1},
                }
            finally:
                self.active -= 1

        return generate()

    def binding(self, agent_session_id: str) -> SessionBinding | None:
        """返回测试 binding。

        Args:
            agent_session_id: 当前测试会话 ID。
        """

        return self._bindings.get(agent_session_id)

    def resolve_owned_session_id(
        self,
        participant: DiscussionParticipant,
    ) -> str | None:
        """按领域 ID 或 owner_ref 返回测试 binding ID。

        Args:
            participant: 要解析的参与者。

        Returns:
            已创建的测试 binding ID；不存在时为 None。
        """

        if participant.agent_session_id in self._bindings:
            return participant.agent_session_id
        return next(
            (
                session_id
                for session_id, binding in self._bindings.items()
                if binding.owner_ref == participant.owner_ref
            ),
            None,
        )

    async def interrupt(self, agent_session_id: str) -> None:
        """测试中断不产生额外事件。

        Args:
            agent_session_id: 当前测试会话 ID。
        """

        del agent_session_id

    async def cancel_elicitation(self, agent_session_id: str) -> bool:
        """测试端口立即确认拒绝交互提问。

        Args:
            agent_session_id: 当前测试会话 ID。

        Returns:
            固定为 True。
        """

        del agent_session_id
        return True

    async def answer_elicitation(
        self,
        agent_session_id: str,
        answers: dict[str, str],
    ) -> bool:
        """默认测试端口没有等待提问，返回已接收以满足窄接口。"""

        del agent_session_id, answers
        return True

    def decline_approval(self, agent_session_id: str, request_id: str) -> None:
        """测试端口默认立即拒绝 Codex 审批。

        Args:
            agent_session_id: 当前测试会话 ID。
            request_id: 测试审批请求 ID。
        """

        del agent_session_id, request_id

    async def close(self, agent_session_id: str) -> bool:
        """删除测试 binding 并确认关闭。

        Args:
            agent_session_id: 当前测试会话 ID。

        Returns:
            固定为 True。
        """

        self._bindings.pop(agent_session_id, None)
        return True
