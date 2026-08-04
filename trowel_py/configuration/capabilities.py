"""保存经过真实运行验证的只读模型连接能力表。"""

from __future__ import annotations

from dataclasses import dataclass

from trowel_py.configuration.models import (
    CapabilityView,
    ConnectionKind,
    ProtocolKind,
    RuntimeKind,
    TaskId,
)

CAPABILITY_REGISTRY_VERSION = "provider-runtime-capabilities-v1"

_AGENT_TASKS = (
    TaskId.MEMORY_REFINE,
    TaskId.PROFILE_DISTILL,
    TaskId.MEMORY_DAILY,
    TaskId.MEMORY_WEEKLY,
    TaskId.MEMORY_MONTHLY,
)


@dataclass(frozen=True)
class _Rule:
    """描述一项实测组合允许的 effort 和后台任务。

    Attributes:
        runtime: 真实实验使用的执行引擎或 direct API。
        kind: 真实实验使用的连接种类。
        protocol: 真实实验使用的上游协议。
        model: 实验实际执行的 model ID。
        efforts: 已验证或由 runtime 原生接受的思考强度；空元组表示不单独限制。
        tasks: 可以使用这项组合的后台任务。
        source: 产生能力结论的实验说明。
    """

    runtime: RuntimeKind
    kind: ConnectionKind
    protocol: ProtocolKind
    model: str
    efforts: tuple[str, ...]
    tasks: tuple[TaskId, ...]
    source: str


_RULES = (
    _Rule(
        RuntimeKind.CLAUDE_CODE,
        ConnectionKind.CLAUDE_COMPATIBLE,
        ProtocolKind.ANTHROPIC_MESSAGES,
        "glm-5.2",
        (),
        _AGENT_TASKS,
        "Claude Code 双 GLM 连接隔离与会话恢复实测",
    ),
    _Rule(
        RuntimeKind.CLAUDE_CODE,
        ConnectionKind.CLAUDE_COMPATIBLE,
        ProtocolKind.ANTHROPIC_MESSAGES,
        "deepseek-v4-flash",
        (),
        _AGENT_TASKS,
        "Claude Code + DeepSeek 工具调用与会话恢复实测",
    ),
    _Rule(
        RuntimeKind.CODEX,
        ConnectionKind.CODEX_CUSTOM,
        ProtocolKind.OPENAI_RESPONSES,
        "deepseek-v4-flash",
        ("low", "medium", "high", "xhigh"),
        _AGENT_TASKS,
        "Codex + DeepSeek 工具调用与会话恢复实测",
    ),
    _Rule(
        RuntimeKind.DIRECT_API,
        ConnectionKind.DIRECT_API,
        ProtocolKind.ANTHROPIC_MESSAGES,
        "glm-5.2",
        (),
        (TaskId.MEMORY_WEEKLY,),
        "GLM Anthropic API 每周记忆整理实测",
    ),
)


def capability_for(
    runtime: RuntimeKind,
    kind: ConnectionKind,
    protocol: ProtocolKind,
    model: str,
    effort: str | None,
) -> CapabilityView:
    """返回精确组合的能力结论，不从厂商名或协议成功外推。"""

    for rule in _RULES:
        if (
            rule.runtime == runtime
            and rule.kind == kind
            and rule.protocol == protocol
            and rule.model == model
        ):
            if effort is not None and rule.efforts and effort not in rule.efforts:
                return CapabilityView(
                    status="unsupported",
                    version=CAPABILITY_REGISTRY_VERSION,
                    source=rule.source,
                )
            return CapabilityView(
                status="verified",
                version=CAPABILITY_REGISTRY_VERSION,
                source=rule.source,
                eligible_tasks=rule.tasks,
            )
    return CapabilityView(
        status="unknown",
        version=CAPABILITY_REGISTRY_VERSION,
        source="没有对应 runtime、协议和 model 的真实运行验证记录",
    )
