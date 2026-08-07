"""声明原生 Agent Host 与 Direct API 的后台任务资格边界。"""

from __future__ import annotations

from dataclasses import dataclass

from trowel_py.configuration.models import (
    CapabilityView,
    ConnectionKind,
    ProtocolKind,
    RuntimeKind,
    TaskId,
)

CAPABILITY_REGISTRY_VERSION = "provider-runtime-capabilities-v3"

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
        efforts: 这项后台任务 Gate 实测覆盖的思考强度；空元组表示不单独限制。
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


_DIRECT_API_RULES = (
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
    """返回会话可用性和后台任务资格。

    Claude Code 与 Codex 后台任务复用同一套原生 Agent Host，因此不再用少量 smoke
    结果重复维护逐模型任务白名单。Direct API 没有 runtime 兼容层，仍保持精确门禁。
    """

    if runtime in {RuntimeKind.CLAUDE_CODE, RuntimeKind.CODEX}:
        return CapabilityView(
            status="verified",
            version=CAPABILITY_REGISTRY_VERSION,
            source="后台任务复用已验证的原生 Agent Host；模型与思考强度由 runtime 裁决",
            eligible_tasks=_AGENT_TASKS,
        )
    for rule in _DIRECT_API_RULES:
        if (
            rule.runtime == runtime
            and rule.kind == kind
            and rule.protocol == protocol
            and rule.model == model
        ):
            eligible_tasks = (
                rule.tasks
                if not rule.efforts or effort in rule.efforts
                else ()
            )
            return CapabilityView(
                status="verified",
                version=CAPABILITY_REGISTRY_VERSION,
                source=rule.source,
                eligible_tasks=eligible_tasks,
            )
    return CapabilityView(
        status="unknown",
        version=CAPABILITY_REGISTRY_VERSION,
        source="没有对应 runtime、协议和 model 的真实运行验证记录",
    )
