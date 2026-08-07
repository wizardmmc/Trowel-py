"""Memory MCP 检索器的设置域适配测试。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from trowel_py.configuration.models import (
    ConnectionKind,
    ProtocolKind,
    RuntimeKind,
    TaskId,
)
from trowel_py.configuration.runtime_launch import RuntimeLaunchConfiguration
from trowel_py.llm.client import LLMConfig
from trowel_py.memory.mcp.retriever_factory import (
    MemoryRetrieverConfigurationError,
    TaskBoundRetrieverFactory,
)


def _launch() -> RuntimeLaunchConfiguration:
    """构造使用 Claude 角色名但由 GLM 提供真实模型的启动快照。"""

    return RuntimeLaunchConfiguration(
        connection_id="glm",
        connection_version=1,
        connection_identity_version=2,
        connection_name="GLM",
        runtime=RuntimeKind.CLAUDE_CODE,
        kind=ConnectionKind.CLAUDE_COMPATIBLE,
        protocol=ProtocolKind.ANTHROPIC_MESSAGES,
        model="opus",
        effort="max",
        capability_version="test-v1",
        base_url="https://provider.example/api/anthropic",
        login_directory=None,
        proxy_url=None,
        claude_role_models={"opus": "glm-5.2"},
        codex_catalog=(),
        api_key="private-key",
    )


def test_task_bound_factory_uses_memory_refine_and_resolves_role_model() -> None:
    """在线检索复用 Memory 精炼绑定，不能读取全局 config.toml。"""

    seen_tasks: list[TaskId] = []
    seen_configs: list[LLMConfig] = []
    class Provider:
        """记录配置即可；本测试不发真实模型请求。"""

        def complete(self, system_prompt: str, user_prompt: str) -> str:
            del system_prompt, user_prompt
            return ""

    provider = Provider()

    def resolve(task_id: TaskId) -> RuntimeLaunchConfiguration:
        seen_tasks.append(task_id)
        return _launch()

    def build_provider(config: LLMConfig) -> object:
        seen_configs.append(config)
        return provider

    retriever = TaskBoundRetrieverFactory(
        resolve,
        provider_builder=build_provider,
    )()

    assert seen_tasks == [TaskId.MEMORY_REFINE]
    assert seen_configs == [
        LLMConfig(
            provider="anthropic",
            model="glm-5.2",
            api_key="private-key",
            base_url="https://provider.example/api/anthropic",
        )
    ]
    assert retriever._provider is provider


def test_task_bound_factory_preserves_authenticated_proxy() -> None:
    """在线检索直调模型时不能绕过连接设置中的认证代理。"""

    seen_configs: list[LLMConfig] = []

    class Provider:
        """提供测试所需的最小文本调用接口。"""

        def complete(self, system_prompt: str, user_prompt: str) -> str:
            del system_prompt, user_prompt
            return ""

    def build_provider(config: LLMConfig) -> object:
        """记录检索器收到的完整连接配置。"""

        seen_configs.append(config)
        return Provider()

    launch = replace(
        _launch(),
        proxy_url="http://alice:proxy-secret@proxy.example:8080",
    )

    TaskBoundRetrieverFactory(
        lambda _task: launch,
        provider_builder=build_provider,
    )()

    assert seen_configs[0].proxy_url == (
        "http://alice:proxy-secret@proxy.example:8080"
    )
    assert "proxy-secret" not in repr(seen_configs[0])


def test_task_bound_factory_rejects_protocol_without_direct_adapter() -> None:
    """不支持的任务绑定必须明确失败，不能偷偷读取另一份全局模型配置。"""

    unsupported = replace(
        _launch(),
        runtime=RuntimeKind.CODEX,
        kind=ConnectionKind.CODEX_OFFICIAL,
        protocol=ProtocolKind.CODEX_OFFICIAL,
        base_url=None,
        api_key=None,
        claude_role_models={},
        model="gpt-5.6-luna",
    )

    with pytest.raises(MemoryRetrieverConfigurationError, match="Anthropic Messages"):
        TaskBoundRetrieverFactory(lambda _task: unsupported)()
