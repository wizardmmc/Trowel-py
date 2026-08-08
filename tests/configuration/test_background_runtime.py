"""验证后台任务运行配置的热读取、冻结和 Session Hub 适配。"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from trowel_py.configuration import background_runtime
from trowel_py.configuration.background_runtime import BackgroundRuntimeManager
from trowel_py.configuration.models import (
    ConnectionKind,
    ProtocolKind,
    RuntimeKind,
    TaskId,
)
from trowel_py.configuration.runtime_launch import RuntimeLaunchConfiguration


def _launch(*, model: str = "deepseek-v4-flash") -> RuntimeLaunchConfiguration:
    """构造不接触真实凭据和 runtime 的 Codex 启动快照。"""

    return RuntimeLaunchConfiguration(
        connection_id="connection-a",
        connection_version=4,
        connection_identity_version=3,
        connection_name="DeepSeek",
        runtime=RuntimeKind.CODEX,
        kind=ConnectionKind.CODEX_CUSTOM,
        protocol=ProtocolKind.OPENAI_RESPONSES,
        model=model,
        effort="high",
        capability_version="test-capabilities",
        base_url="https://provider.example/v1",
        login_directory=None,
        proxy_url=None,
        claude_role_models={},
        codex_catalog=(),
        api_key="test-secret",
    )


class FakeSessionHub:
    """记录后台适配器提交的最小 Session Hub 调用。"""

    def __init__(self) -> None:
        """初始化创建、输入和删除记录。"""

        self.requests: list[tuple[Any, str | None]] = []
        self.prompts: list[tuple[str, str]] = []
        self.deleted: list[str] = []

    async def create_complete_session(
        self,
        request: Any,
        *,
        bootstrap_context: str | None = None,
    ) -> SimpleNamespace:
        """保存创建条件并返回稳定测试会话身份。"""

        self.requests.append((request, bootstrap_context))
        return SimpleNamespace(session_id=f"background-{len(self.requests)}")

    async def stream(self, session_id: str, text: str):
        """产生一段文本和正常终态。"""

        self.prompts.append((session_id, text))
        yield {"type": "text", "payload": {"text": "result"}}
        yield {"type": "finished", "payload": {}}

    async def delete(self, session_id: str) -> bool:
        """记录会话清理。"""

        self.deleted.append(session_id)
        return True


async def test_background_host_uses_frozen_connection_without_user_injections(
    tmp_path: Path,
) -> None:
    """后台 host 应走统一 Hub，并关闭 Memory、Profile、Self 和 Agent MCP。"""

    hub = FakeSessionHub()
    manager = BackgroundRuntimeManager(
        lambda _task: _launch(),
        hub,
        asyncio.get_running_loop(),
    )
    frozen = manager.freeze(TaskId.MEMORY_REFINE)
    host = frozen.host_factory("source-a", tmp_path)

    events = [event async for event in host.send("refine")]
    await host.close()

    request, bootstrap = hub.requests[0]
    assert [event.type for event in events] == ["text", "finished"]
    assert bootstrap is None
    assert request.session_kind == "background"
    assert request.connection_id == "connection-a"
    assert request.permission_preset == "danger-full-access"
    assert request.memory_enabled is False
    assert request.profile_enabled is False
    assert request.self_enabled is False
    assert request.agent_mcp_enabled is False
    assert hub.deleted == ["background-1"]


async def test_background_provider_runs_on_main_loop_and_returns_text(
    tmp_path: Path,
) -> None:
    """同步旧 provider 接口应桥接到主循环上的托管 Agent 会话。"""

    hub = FakeSessionHub()
    manager = BackgroundRuntimeManager(
        lambda _task: _launch(),
        hub,
        asyncio.get_running_loop(),
    )
    provider = manager.freeze(TaskId.MEMORY_DAILY).provider(workdir=tmp_path)

    result = await asyncio.to_thread(provider.complete, "system", "user")

    assert result == "result"
    assert hub.requests[0][1] == "system"
    assert hub.prompts == [("background-1", "user")]
    assert hub.deleted == ["background-1"]


async def test_each_background_run_resolves_a_new_snapshot() -> None:
    """设置修改只影响下一次 freeze，已经返回的运行快照保持不变。"""

    launches = [_launch(), replace(_launch(), model="deepseek-v4-pro")]
    observed_tasks: list[TaskId] = []

    def resolve(task_id: TaskId) -> RuntimeLaunchConfiguration:
        """按调用次序返回两版运行配置。"""

        observed_tasks.append(task_id)
        return launches[len(observed_tasks) - 1]

    manager = BackgroundRuntimeManager(
        resolve,
        FakeSessionHub(),
        asyncio.get_running_loop(),
    )

    first = manager.freeze(TaskId.PROFILE_DISTILL)
    second = manager.freeze(TaskId.PROFILE_DISTILL)

    assert first.launch.model == "deepseek-v4-flash"
    assert second.launch.model == "deepseek-v4-pro"
    assert observed_tasks == [TaskId.PROFILE_DISTILL, TaskId.PROFILE_DISTILL]


async def test_direct_provider_receives_complete_proxy_launch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Direct API 后台任务必须把认证代理保留到共享 provider 工厂。"""

    direct = replace(
        _launch(model="direct-model"),
        runtime=RuntimeKind.DIRECT_API,
        kind=ConnectionKind.DIRECT_API,
        protocol=ProtocolKind.ANTHROPIC_MESSAGES,
        proxy_url="http://alice:proxy-secret@proxy.example:8080",
    )
    captured: list[RuntimeLaunchConfiguration] = []
    provider = object()

    def build(launch: RuntimeLaunchConfiguration) -> object:
        """记录 direct provider 收到的完整冻结配置。"""

        captured.append(launch)
        return provider

    monkeypatch.setattr(background_runtime, "build_anthropic_text_provider", build)
    manager = BackgroundRuntimeManager(
        lambda _task: direct,
        FakeSessionHub(),
        asyncio.get_running_loop(),
    )

    result = manager.freeze(TaskId.MEMORY_WEEKLY).provider(workdir=tmp_path)

    assert result is provider
    assert captured == [direct]
    assert captured[0].proxy_url == (
        "http://alice:proxy-secret@proxy.example:8080"
    )
    assert "proxy-secret" not in repr(captured[0])
