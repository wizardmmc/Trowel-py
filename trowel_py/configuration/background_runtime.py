"""把后台任务绑定适配为 Trowel 托管的 Agent host 或直接 API provider。"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol, TypeVar

from trowel_py.agent_host.schemas import CreateAgentSessionRequest
from trowel_py.agent_host.binding import SessionBinding
from trowel_py.configuration.models import RuntimeKind, TaskId
from trowel_py.configuration.runtime_launch import RuntimeLaunchConfiguration
from trowel_py.configuration.text_provider import (
    TextProviderConfigurationError,
    build_anthropic_text_provider,
)
from trowel_py.llm.client import LLMProvider

_T = TypeVar("_T")


class BackgroundRuntimeError(RuntimeError):
    """表示后台任务没有可执行的绑定，或绑定的 runtime 未正常结束。"""


class SessionHubPort(Protocol):
    """声明后台适配器实际使用的 Session Hub 最小接口。"""

    async def create_complete_session(
        self,
        request: CreateAgentSessionRequest,
        *,
        bootstrap_context: str | None = None,
    ) -> SessionBinding:
        """创建完整会话并返回持久 binding。"""

    def stream(self, session_id: str, text: str) -> AsyncIterator[dict[str, Any]]:
        """执行一轮并返回统一事件流。"""

    async def delete(self, session_id: str) -> bool:
        """关闭并删除后台会话 binding。"""


TaskLaunchResolver = Callable[[TaskId], RuntimeLaunchConfiguration]


@dataclass(frozen=True)
class FrozenBackgroundRuntime:
    """保存一次后台任务开始时冻结的运行配置与启动依赖。

    Attributes:
        task_id: 本次执行所属的稳定后台任务。
        launch: 开跑时从设置域解析出的秘密启动快照。
        hub: 统一创建 Claude Code 或 Codex 会话的端口。
        main_loop: Session Hub 及 runtime manager 所属的应用事件循环。
    """

    task_id: TaskId
    launch: RuntimeLaunchConfiguration
    hub: SessionHubPort
    main_loop: asyncio.AbstractEventLoop

    def host_factory(self, _source_id: object, workdir: Path) -> ManagedAgentHost:
        """为一项提炼来源创建延迟启动的托管 Agent host。

        Args:
            _source_id: 上游提炼管线的来源身份；runtime 不消费该值。
            workdir: 本次提炼唯一允许写入的工作目录。
        """

        return ManagedAgentHost(self, workdir)

    def provider(self, *, workdir: Path) -> LLMProvider:
        """创建周月整理或每日压缩使用的文本生成接口。

        Args:
            workdir: Agent runtime 执行文本任务时使用的受控工作目录。
        """

        if self.launch.runtime is RuntimeKind.DIRECT_API:
            try:
                return build_anthropic_text_provider(self.launch)
            except TextProviderConfigurationError as exc:
                raise BackgroundRuntimeError(str(exc)) from exc
        return ManagedAgentProvider(self, workdir)


class BackgroundRuntimeManager:
    """在任务边界热读取绑定，并把单次运行冻结为独立值对象。"""

    def __init__(
        self,
        resolver: TaskLaunchResolver,
        hub: SessionHubPort,
        main_loop: asyncio.AbstractEventLoop,
    ) -> None:
        """保存无状态配置解析端口与应用 runtime 依赖。

        Args:
            resolver: 每次调用都从短数据库连接读取任务绑定的函数。
            hub: Trowel 统一会话管理端口。
            main_loop: Hub 和 Codex manager 所属的应用事件循环。
        """

        self._resolver = resolver
        self._hub = hub
        self._main_loop = main_loop

    def freeze(self, task_id: TaskId) -> FrozenBackgroundRuntime:
        """解析一次当前绑定；后续设置修改不影响返回快照。"""

        return FrozenBackgroundRuntime(
            task_id=task_id,
            launch=self._resolver(task_id),
            hub=self._hub,
            main_loop=self._main_loop,
        )


class ManagedAgentHost:
    """把 Session Hub 事件字典适配为现有提炼管线的 host 协议。"""

    def __init__(self, runtime: FrozenBackgroundRuntime, workdir: Path) -> None:
        """保存冻结配置并延迟到首轮输入时创建原生会话。

        Args:
            runtime: 单次后台任务冻结的运行配置。
            workdir: Agent 可以读取来源并写出结构化草稿的目录。
        """

        self._runtime = runtime
        self._workdir = workdir
        self.session_id = uuid.uuid4().hex
        self.model = runtime.launch.model
        self.effort = runtime.launch.effort
        self.runtime = runtime.launch.runtime.value
        self._opened = False

    async def send(self, prompt: str) -> AsyncIterator[SimpleNamespace]:
        """在应用主循环执行完整 Agent 轮次并转发兼容事件对象。"""

        await self._open()
        events: list[dict[str, Any]] = await _await_on_loop(
            self._runtime.main_loop,
            _collect_events(self._runtime.hub, self.session_id, prompt),
        )
        for event in events:
            yield SimpleNamespace(
                type=event.get("type"),
                payload=event.get("payload", {}),
            )

    async def close(self) -> None:
        """关闭已经创建的后台会话；尚未启动时不产生 runtime 资源。"""

        if not self._opened:
            return
        await _await_on_loop(
            self._runtime.main_loop,
            self._runtime.hub.delete(self.session_id),
        )
        self._opened = False

    async def _open(self, *, bootstrap_context: str | None = None) -> None:
        """按冻结连接和最小注入条件创建一次后台会话。"""

        if self._opened:
            return
        request = _background_request(self._runtime.launch, self._workdir)
        binding: SessionBinding = await _await_on_loop(
            self._runtime.main_loop,
            self._runtime.hub.create_complete_session(
                request,
                bootstrap_context=bootstrap_context,
            ),
        )
        self.session_id = str(binding.session_id)
        self._opened = True


class ManagedAgentProvider:
    """把一次托管 Agent 轮次适配为同步 ``LLMProvider.complete``。"""

    def __init__(self, runtime: FrozenBackgroundRuntime, workdir: Path) -> None:
        """保存周月或每日任务开始时的冻结 runtime 与工作目录。

        Args:
            runtime: 单次任务冻结的运行配置。
            workdir: Agent runtime 的受控工作目录。
        """

        self._runtime = runtime
        self._workdir = workdir

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """在应用主循环执行 Agent，并拼接其文本事件作为模型回复。"""

        if _running_on(self._runtime.main_loop):
            raise BackgroundRuntimeError(
                "托管后台 provider 不能阻塞 Session Hub 所属事件循环"
            )
        future = asyncio.run_coroutine_threadsafe(
            self._complete(system_prompt, user_prompt),
            self._runtime.main_loop,
        )
        return future.result()

    async def _complete(self, system_prompt: str, user_prompt: str) -> str:
        """创建一次性后台会话，消费终态后保证清理。"""

        host = ManagedAgentHost(self._runtime, self._workdir)
        try:
            await host._open(bootstrap_context=system_prompt)
            events = await _collect_events(
                self._runtime.hub,
                host.session_id,
                user_prompt,
            )
            if not any(event.get("type") == "finished" for event in events):
                raise BackgroundRuntimeError("后台 Agent 未正常完成")
            text = "".join(
                str(event.get("payload", {}).get("text", ""))
                for event in events
                if event.get("type") == "text"
            )
            if not text:
                raise BackgroundRuntimeError("后台 Agent 没有返回文本")
            return text
        finally:
            await host.close()


def _background_request(
    launch: RuntimeLaunchConfiguration,
    workdir: Path,
) -> CreateAgentSessionRequest:
    """把秘密启动快照转换为不继承用户上下文的内部会话请求。"""

    common: dict[str, Any] = {
        "runtime": launch.runtime.value,
        "connection_id": launch.connection_id,
        "workdir": str(workdir),
        "model": launch.model,
        "effort": launch.effort,
        "memory_enabled": False,
        "profile_enabled": False,
        "self_enabled": False,
        "session_kind": "background",
        "memory_eligibility": False,
        "agent_mcp_enabled": False,
        "expected_connection_identity_version": launch.connection_identity_version,
    }
    if launch.runtime is RuntimeKind.CLAUDE_CODE:
        common["permission_mode"] = "bypassPermissions"
    elif launch.runtime is RuntimeKind.CODEX:
        common["permission_preset"] = "danger-full-access"
    else:
        raise BackgroundRuntimeError("direct API 不能创建 Agent host")
    return CreateAgentSessionRequest(**common)


async def _collect_events(
    hub: SessionHubPort,
    session_id: str,
    prompt: str,
) -> list[dict[str, Any]]:
    """完整消费一轮统一事件，确保调用方观察到真实终态。"""

    return [event async for event in hub.stream(session_id, prompt)]


async def _await_on_loop(
    loop: asyncio.AbstractEventLoop,
    coroutine: Coroutine[Any, Any, _T],
) -> _T:
    """从任意 worker 事件循环安全等待应用主循环上的协程。"""

    if _running_on(loop):
        return await coroutine
    future = asyncio.run_coroutine_threadsafe(coroutine, loop)
    return await asyncio.wrap_future(future)


def _running_on(loop: asyncio.AbstractEventLoop) -> bool:
    """判断当前同步或异步调用是否已经位于目标事件循环。"""

    try:
        return asyncio.get_running_loop() is loop
    except RuntimeError:
        return False
