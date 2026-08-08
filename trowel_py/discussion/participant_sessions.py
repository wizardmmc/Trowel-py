"""把研讨 participant 的会话生命周期收窄为与 Session Hub 解耦的端口。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

from trowel_py.agent_host.binding import Runtime, SessionBinding
from trowel_py.agent_host.hub import (
    FrozenConnectionExpectation,
    SessionHub,
    SessionHubError,
    SessionNotFoundError,
)
from trowel_py.agent_host.schemas import CreateAgentSessionRequest
from trowel_py.discussion.errors import DiscussionRuntimeError
from trowel_py.discussion.models import (
    DiscussionParticipant,
    ParticipantAttemptHistoryRequest,
)


@dataclass(frozen=True)
class ParticipantSession:
    """返回协调器真正需要的 participant 会话身份。

    Attributes:
        agent_session_id: 当前 Session Hub 会话 ID。
        native_session_id: 可跨应用进程恢复的原生会话或 thread ID。
        capability_version: 连接能力表版本。
        capability_source: 允许该 runtime/model 组合的证据说明。
    """

    agent_session_id: str
    native_session_id: str | None
    capability_version: str | None
    capability_source: str | None


class ParticipantLifecyclePort(Protocol):
    """约束 participant binding 的创建、定位和关闭操作。"""

    async def ensure_session(
        self,
        participant: DiscussionParticipant,
        *,
        workdir: str,
    ) -> ParticipantSession:
        """创建、认领或恢复一个 discussion participant 会话。"""
        ...

    def binding(self, agent_session_id: str) -> SessionBinding | None:
        """返回当前已持久化的 Session Hub binding。"""
        ...

    def resolve_owned_session_id(
        self,
        participant: DiscussionParticipant,
    ) -> str | None:
        """按领域记录或 owner_ref 找到 participant 当前 binding。"""
        ...

    async def close(self, agent_session_id: str) -> bool:
        """关闭并删除 participant 的当前 Trowel binding。"""
        ...


class ParticipantTurnPort(Protocol):
    """约束协调器唯一消费 participant turn 的数据流入口。"""

    def run_turn(
        self,
        agent_session_id: str,
        prompt: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """启动一轮普通输入并持续产出统一事件到流结束。"""
        ...


class ParticipantInteractionPort(Protocol):
    """约束等待中的 participant turn 可以接受的最小控制操作。"""

    async def interrupt(self, agent_session_id: str) -> None:
        """请求中断当前 participant turn。"""
        ...

    async def cancel_elicitation(self, agent_session_id: str) -> bool:
        """拒绝 discussion 内无人能够回答的交互提问。"""
        ...

    async def answer_elicitation(
        self,
        agent_session_id: str,
        answers: dict[str, str],
    ) -> bool:
        """回答 discussion 内明确允许的 AskUserQuestion。"""
        ...

    def decline_approval(self, agent_session_id: str, request_id: str) -> None:
        """拒绝 discussion 内无人能够批准的 Codex 请求。"""
        ...


class ParticipantHistoryPort(Protocol):
    """约束从原生记录读取一个 participant attempt 的只读操作。"""

    async def read_attempt_history(
        self,
        request: ParticipantAttemptHistoryRequest,
    ) -> list[dict[str, Any]]:
        """返回一个 attempt 对应的统一 AgentEvent 序列。"""
        ...



class ParticipantSessionPort(
    ParticipantLifecyclePort,
    ParticipantTurnPort,
    ParticipantInteractionPort,
    ParticipantHistoryPort,
    Protocol,
):
    """组合协调器当前需要的四个窄端口，便于替身按职责实现。"""


class AgentHostParticipantSessionAdapter:
    """通过 Session Hub 内部接口管理 discussion participant 原生会话。"""

    def __init__(self, hub: SessionHub) -> None:
        """绑定应用唯一 Session Hub。

        Args:
            hub: 已完成 runtime 和连接 resolver 装配的 Session Hub。
        """

        self._hub = hub

    async def ensure_session(
        self,
        participant: DiscussionParticipant,
        *,
        workdir: str,
    ) -> ParticipantSession:
        """优先认领 owner_ref binding，断线时用同一原生 ID 建新 binding。

        Args:
            participant: 已冻结 runtime、连接、模型和 owner_ref 的参与者。
            workdir: discussion 创建时冻结的工作目录。

        Returns:
            当前可运行的 participant 会话身份。

        Raises:
            DiscussionRuntimeError: 旧资源无法收敛或新会话创建失败。
        """

        owned = self._hub.store.find_by_owner_ref(participant.owner_ref)
        if owned is not None:
            self._validate_frozen_binding(participant, owned)
            try:
                self._hub.require_event_session(owned.session_id)
            except SessionNotFoundError:
                result = await self._hub.close_result(
                    owned.session_id,
                    delete_binding=True,
                )
                if result.status not in {"closed", "not_found"}:
                    raise DiscussionRuntimeError(
                        "原参与者会话尚未完成资源对账，暂时不能恢复研讨"
                    )
            else:
                return self._from_binding(owned)
        resume_from = participant.native_session_id
        if resume_from is None and owned is not None:
            resume_from = owned.native_session_id
        request = CreateAgentSessionRequest(
            runtime=participant.runtime.value,
            connection_id=participant.connection_id,
            workdir=workdir,
            resume_from=resume_from,
            model=participant.model,
            effort=participant.effort,
            permission_mode=participant.permission_mode,
            permission_preset=participant.permission_preset,
            memory_enabled=participant.memory_enabled,
            profile_enabled=participant.profile_enabled,
            self_enabled=participant.self_enabled,
            session_kind="discussion",
            memory_eligibility=False,
            agent_mcp_enabled=False,
            delegation_depth=0,
            owner_ref=participant.owner_ref,
        )
        try:
            binding = await self._hub.create_complete_session(
                request,
                frozen_connection=FrozenConnectionExpectation(
                    connection_identity_version=(
                        participant.connection_identity_version
                    ),
                    capability_version=participant.capability_version,
                    capability_source=participant.capability_source,
                ),
            )
        except SessionHubError as exc:
            raise DiscussionRuntimeError() from exc
        try:
            self._validate_frozen_binding(participant, binding)
        except DiscussionRuntimeError:
            await self._hub.close_result(binding.session_id, delete_binding=True)
            raise
        return self._from_binding(binding)

    async def read_attempt_history(
        self,
        request: ParticipantAttemptHistoryRequest,
    ) -> list[dict[str, Any]]:
        """按持久原生身份读取一轮，不要求 participant binding 仍存在。

        Args:
            request: repository 验证归属后生成的历史定位事实。

        Returns:
            只含该 attempt 的统一 AgentEvent 序列。

        Raises:
            DiscussionRuntimeError: 原生历史不可用或读取失败。
        """

        if request.native_session_id is None:
            return []
        session_id = request.agent_session_id or f"discussion-attempt-{request.id}"
        try:
            return await self._hub.history_by_native(
                session_id=session_id,
                runtime=request.runtime,
                native_session_id=request.native_session_id,
                workdir=request.workdir,
                root_turn_id=request.root_turn_id,
                input_hash=request.input_hash,
                input_occurrence=request.input_occurrence,
            )
        except SessionHubError as exc:
            raise DiscussionRuntimeError("参与者原生历史暂时不可用") from exc

    def run_turn(
        self,
        agent_session_id: str,
        prompt: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """把确定性 prompt 直接作为普通输入交给 Session Hub 统一流。

        Args:
            agent_session_id: 当前 participant 的 Trowel 会话 ID。
            prompt: 本轮所有参与者共用的精确文本。

        Returns:
            必须由协调器消费到根终态或异常结束的事件迭代器。
        """

        async def generate() -> AsyncIterator[dict[str, Any]]:
            """只把 Session Hub 明确的运行时故障归一化为 discussion 断联。"""

            try:
                async for event in self._hub.stream(agent_session_id, prompt):
                    yield event
            except SessionHubError as exc:
                raise DiscussionRuntimeError("参与者运行工具连接异常结束") from exc

        return generate()

    def binding(self, agent_session_id: str) -> SessionBinding | None:
        """读取当前 participant binding。

        Args:
            agent_session_id: 当前 Trowel 会话 ID。

        Returns:
            对应 binding；不存在时为 None。
        """

        return self._hub.get(agent_session_id)

    def resolve_owned_session_id(
        self,
        participant: DiscussionParticipant,
    ) -> str | None:
        """优先返回领域已登记 ID，再以 owner_ref 找回跨存储孤儿 binding。

        Args:
            participant: 带稳定 owner_ref 的参与者快照。

        Returns:
            当前 binding ID；领域记录和 BindingStore 都没有时为 None。
        """

        owned = self._hub.store.find_by_owner_ref(participant.owner_ref)
        if participant.agent_session_id is not None:
            recorded = self._hub.store.get(participant.agent_session_id)
            if recorded is not None and recorded.owner_ref != participant.owner_ref:
                raise DiscussionRuntimeError(
                    "参与者领域记录指向了其他 owner 的会话，需要资源对账"
                )
            if recorded is not None:
                if owned is not None and owned.session_id != recorded.session_id:
                    raise DiscussionRuntimeError(
                        "参与者 owner 同时对应不一致的会话，需要资源对账"
                    )
                return recorded.session_id
        return owned.session_id if owned is not None else None

    async def interrupt(self, agent_session_id: str) -> None:
        """把中断请求交给 Session Hub，不能把 ACK 当终态。

        Args:
            agent_session_id: 当前 Trowel 会话 ID。
        """

        try:
            await self._hub.interrupt(agent_session_id)
        except SessionHubError as exc:
            raise DiscussionRuntimeError("参与者中断结果未知") from exc

    async def cancel_elicitation(self, agent_session_id: str) -> bool:
        """拒绝 CC 的 AskUserQuestion，避免私有会话永久等待。

        Args:
            agent_session_id: 当前 participant 的 Trowel 会话 ID。

        Returns:
            拒绝控制消息已经写入时为 True。

        Raises:
            DiscussionRuntimeError: 会话不存在、不是 CC 或控制消息写入失败。
        """

        try:
            return await self._hub.cancel_elicitation(agent_session_id)
        except SessionHubError as exc:
            raise DiscussionRuntimeError("参与者交互提问无法取消") from exc

    async def answer_elicitation(
        self,
        agent_session_id: str,
        answers: dict[str, str],
    ) -> bool:
        """把用户答案交回 participant 当前 AskUserQuestion。

        Args:
            agent_session_id: 当前 participant 的 Trowel 会话 ID。
            answers: 问题正文到用户答案的对应表。

        Returns:
            回答控制消息已经写入时为 True。

        Raises:
            DiscussionRuntimeError: 会话、提问或控制写入已经不可用。
        """

        try:
            return await self._hub.answer_elicitation(agent_session_id, answers)
        except SessionHubError as exc:
            raise DiscussionRuntimeError("参与者交互提问无法回答") from exc

    def decline_approval(self, agent_session_id: str, request_id: str) -> None:
        """立即拒绝 Codex 审批，避免等待通用界面的十分钟超时。

        Args:
            agent_session_id: 当前 participant 的 Trowel 会话 ID。
            request_id: approval_request 事件携带的稳定请求 ID。

        Raises:
            DiscussionRuntimeError: 请求不存在、归属不符或已经结束。
        """

        try:
            self._hub.decline_request(agent_session_id, request_id)
        except SessionHubError as exc:
            raise DiscussionRuntimeError("参与者审批请求无法拒绝") from exc

    async def close(self, agent_session_id: str) -> bool:
        """关闭 participant runtime 并只在核验归零后删除 binding。

        Args:
            agent_session_id: 当前 Trowel 会话 ID。

        Returns:
            资源和 binding 都已收敛时为 True。
        """

        try:
            result = await self._hub.close_result(
                agent_session_id,
                delete_binding=True,
            )
        except SessionHubError as exc:
            raise DiscussionRuntimeError("参与者会话关闭结果未知") from exc
        return result.status in {"closed", "not_found"}

    @staticmethod
    def _from_binding(binding: SessionBinding) -> ParticipantSession:
        """把完整 binding 投影成 coordinator 需要的最小身份。

        Args:
            binding: 当前 participant binding。

        Returns:
            不含连接路径或权限细节的会话身份。
        """

        return ParticipantSession(
            agent_session_id=binding.session_id,
            native_session_id=binding.native_session_id,
            capability_version=binding.configuration_capability_version,
            capability_source=binding.configuration_capability_source,
        )

    @staticmethod
    def _validate_frozen_binding(
        participant: DiscussionParticipant,
        binding: SessionBinding,
    ) -> None:
        """拒绝把创建后已变化的连接身份或能力静默换进 participant。

        Args:
            participant: discussion 创建事务冻结的参与者配置。
            binding: 已认领或刚创建的 Session Hub binding。

        Raises:
            DiscussionRuntimeError: runtime、连接、模型、身份版本或能力证据漂移。
        """

        actual = (
            binding.runtime,
            binding.connection_id,
            binding.requested_model,
            binding.requested_effort,
            binding.permission if participant.runtime is Runtime.CLAUDE_CODE else None,
            binding.permission_preset if participant.runtime is Runtime.CODEX else None,
            binding.memory_enabled,
            binding.profile_enabled,
            binding.self_enabled,
            binding.connection_identity_version,
            binding.configuration_capability_version,
            binding.configuration_capability_source,
        )
        expected = (
            participant.runtime,
            participant.connection_id,
            participant.model,
            participant.effort,
            participant.permission_mode,
            participant.permission_preset,
            participant.memory_enabled,
            participant.profile_enabled,
            participant.self_enabled,
            participant.connection_identity_version,
            participant.capability_version,
            participant.capability_source,
        )
        if actual != expected:
            raise DiscussionRuntimeError(
                "参与者创建时冻结的连接身份或能力已经变化，需要人工重新绑定"
            )
