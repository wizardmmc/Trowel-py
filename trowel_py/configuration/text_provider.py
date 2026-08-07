"""把冻结运行配置收窄为可直接调用的文本模型 provider。"""

from __future__ import annotations

from trowel_py.configuration.models import CLAUDE_ROLE_NAMES, ProtocolKind
from trowel_py.configuration.runtime_launch import RuntimeLaunchConfiguration
from trowel_py.llm.client import AnthropicProvider, LLMConfig, LLMProvider


class TextProviderConfigurationError(RuntimeError):
    """表示冻结运行配置不能安全转换成直接文本调用。"""


def anthropic_llm_config(launch: RuntimeLaunchConfiguration) -> LLMConfig:
    """保留模型、凭据、上游和代理，构造 Anthropic 文本调用配置。

    Args:
        launch: 设置域在任务开始时冻结的完整启动事实。

    Returns:
        不丢失连接级代理的 Anthropic SDK 配置。

    Raises:
        TextProviderConfigurationError: 协议、凭据或 Claude 角色模型不可用。
    """

    if launch.protocol is not ProtocolKind.ANTHROPIC_MESSAGES:
        raise TextProviderConfigurationError("当前文本调用需要 Anthropic Messages 连接")
    if not launch.api_key or not launch.base_url:
        raise TextProviderConfigurationError("文本调用连接缺少启动凭据")
    model = launch.claude_role_models.get(launch.model, launch.model)
    if launch.model in CLAUDE_ROLE_NAMES and model == launch.model:
        raise TextProviderConfigurationError(
            f"文本调用无法解析 Claude 角色模型 {launch.model!r}"
        )
    return LLMConfig(
        provider="anthropic",
        model=model,
        api_key=launch.api_key,
        base_url=launch.base_url,
        proxy_url=launch.proxy_url,
    )


def build_anthropic_text_provider(
    launch: RuntimeLaunchConfiguration,
) -> LLMProvider:
    """组合冻结启动配置与 Anthropic provider 实现。"""

    return AnthropicProvider(anthropic_llm_config(launch))
