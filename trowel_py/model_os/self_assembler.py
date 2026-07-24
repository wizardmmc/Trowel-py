"""根据 runtime 事实组装 Self Manifest，并渲染到 memory 注入之前。

稳定身份头在同一版本内保持字节不变且不含时间戳，动态调用信息放在尾部。未注入
的 memory/profile 只暴露开关状态，不携带路径、检索指针或正文。
"""

from __future__ import annotations

import logging

from trowel_py.model_os.types import SelfManifest, SubsystemState

logger = logging.getLogger(__name__)

# 身份版本只能随系统升级调整，不能由模型自行修改。
IDENTITY = "Trowel"
VERSION = "trowel-os-v0"
CONTINUITY_NOTE = "本次行动是持续主体的一段活动"

# 这里只声明 Trowel 系统能力；runtime 自带的工具/MCP 不重复进入身体图式。
TROWEL_SUBSYSTEMS: tuple[str, ...] = (
    "memory",
    "profile",
    "model_os",
    "dual_runtime",
    "todo_loop",
)

_SUBSYSTEM_LABELS: dict[str, str] = {
    "memory": "记忆系统：跨会话记忆",
    "profile": "用户画像：合作者的画像",
    "model_os": "Model OS 内核：跨会话持续身份",
    "dual_runtime": (
        "双 runtime：系统可分别启动 CC 与 Codex；"
        "跨 runtime 调用须走 Trowel Agent HTTP API，不是 runtime 自带能力"
    ),
    "todo_loop": "todo loop：任务跟踪",
}

# 不含右括号的前缀可跨版本识别 Memory 对 Self 命名空间的冒用。
_SELF_NAMESPACE_MARKER = "# 关于你（Self"


def build_self_manifest(
    *,
    runtime: str,
    model: str | None,
    effort: str | None,
    memory_enabled: bool,
    profile_enabled: bool,
    permission_preset: str | None = None,
    task_id: str | None = None,
    episode_id: str | None = None,
    native_session_id: str | None = None,
) -> SelfManifest:
    """组装非持久化的 Self Manifest。

    ``model`` 和 ``effort`` 为 ``None`` 表示 runtime 尚未提供事实，不能用缓存值
    掩盖；未绑定 Task/Episode 时，对应位置指针保持 ``None``。
    """

    return SelfManifest(
        identity=IDENTITY,
        version=VERSION,
        continuity_note=CONTINUITY_NOTE,
        runtime=runtime,
        model=model,
        effort=effort,
        subsystems=TROWEL_SUBSYSTEMS,
        memory_state=(
            SubsystemState.INJECTED if memory_enabled else SubsystemState.OFF
        ),
        profile_state=(
            SubsystemState.INJECTED if profile_enabled else SubsystemState.OFF
        ),
        native_tools_note=_native_tools_note(runtime),
        authorization_scope=_authorization_scope(permission_preset),
        task_id=task_id,
        episode_id=episode_id,
        native_session_id=native_session_id,
    )


def _native_tools_note(runtime: str) -> str:
    return f"本次 runtime 的工具与 MCP 由 {runtime} 自带，不在 trowel 层重复"


def _authorization_scope(permission_preset: str | None) -> str:
    """当前只透传 runtime 的授权模式，不推导具体动作权限。"""

    if permission_preset is None:
        return "授权模式：未定"
    return f"授权模式：{permission_preset}"


def _state_line(state: SubsystemState) -> str:
    if state == SubsystemState.INJECTED:
        return "本次内容已注入"
    return "本次内容未注入"


def render_self_injection(manifest: SelfManifest) -> str:
    """渲染 Self 注入；稳定身份头在动态调用信息之前，以复用 prompt cache。"""

    runtime = manifest.runtime
    model_line = manifest.model if manifest.model is not None else "未定"
    effort_line = manifest.effort if manifest.effort is not None else "未定"

    body_items: list[str] = []
    for subsystem in manifest.subsystems:
        label = _SUBSYSTEM_LABELS.get(subsystem, subsystem)
        if subsystem == "memory":
            state = _state_line(manifest.memory_state)
        elif subsystem == "profile":
            state = _state_line(manifest.profile_state)
        else:
            state = "常驻"
        body_items.append(f"- {label}（{state}）")
    body_text = "\n".join(body_items)

    return (
        f"# 关于你（Self · {manifest.version}）\n\n"
        f"你是 {manifest.identity} 的一段脑力活动。{manifest.continuity_note}。\n"
        f"{manifest.identity} 是跨会话持续存在的系统。\n\n"
        "## 本次调用\n"
        f"- runtime: {runtime}\n"
        f"- 你（{runtime} agent）是 {manifest.identity} 本次调用的一段脑力活动\n"
        f"- model: {model_line}\n"
        f"- effort: {effort_line}\n"
        f"- {manifest.native_tools_note}\n"
        f"- {manifest.authorization_scope}\n\n"
        "## Trowel 的身体\n"
        f"{body_text}\n\n"
        "## 合作\n"
        f"{manifest.identity} 正在跟一个具体的人合作。你在为这个人工作。"
    )


def build_session_injection(
    *,
    self_enabled: bool,
    memory_text: str,
    runtime: str,
    model: str | None,
    effort: str | None,
    memory_enabled: bool,
    profile_enabled: bool,
    permission_preset: str | None = None,
) -> str:
    """按 Self 在前、Memory 在后的顺序组装会话注入。

    CC 与 Codex 共用此入口；本模块接收已经生成的 ``memory_text``，不依赖 Memory
    领域。关闭 Self 时原样返回 ``memory_text``，空字符串由调用方解释为不注入。
    """

    if not self_enabled:
        return memory_text
    manifest = build_self_manifest(
        runtime=runtime,
        model=model,
        effort=effort,
        memory_enabled=memory_enabled,
        profile_enabled=profile_enabled,
        permission_preset=permission_preset,
    )
    self_text = render_self_injection(manifest)
    # 这里只告警，不改写 Memory；规范 Self 保持在前，源头过滤由 Memory 领域负责。
    if _SELF_NAMESPACE_MARKER in memory_text:
        logger.warning(
            "memory injection carries the reserved Self namespace marker "
            "%r; a model may have smuggled Self-level text via Memory. "
            "Canonical Self stays first; memory-write-layer enforcement is "
            "deferred.",
            _SELF_NAMESPACE_MARKER,
        )
    parts = [part for part in (self_text, memory_text) if part]
    return "\n\n".join(parts)
