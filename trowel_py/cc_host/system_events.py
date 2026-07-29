"""将 CC 的 `system` 消息翻译为 Trowel 运行时事件。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from trowel_py.cc_host.schemas import (
    CompactBoundaryEvent,
    HookEvent,
    LocalCommandEvent,
    RetryingEvent,
    SessionStartedEvent,
    StatusEvent,
    SubagentProgressEvent,
    ThinkingProgressEvent,
    TrowelEvent,
)

_IGNORE_SYSTEM_SUBTYPES = frozenset(
    {
        "post_turn_summary",
        # task_updated 与 task_notification 重复且缺少关联 ID，不单独映射。
        "task_updated",
        "session_state_changed",
        "files_persisted",
        "elicitation_complete",
        "prompt_suggestion",
        "mcp_message",
    }
)
_IGNORE_SYSTEM_PREFIXES = ("streamlined_",)


def translate_system_event(
    ev: dict[str, Any],
    *,
    as_text_fn: Callable[[Any], str],
    logger: logging.Logger,
) -> list[TrowelEvent]:
    """按 `system.subtype` 生成对应事件。

    已知但无消费用途的 subtype 静默忽略，未知 subtype 记录 debug 日志后丢弃。

    Args:
        ev: CC `system` 消息字典。
        as_text_fn: 将 `local_command_output.content` 转成文本的函数。
        logger: 记录未知 subtype 的日志器。

    Returns:
        翻译得到的单个事件列表；消息被忽略或无法映射时返回空列表。
    """

    sub = ev.get("subtype")
    if sub in _IGNORE_SYSTEM_SUBTYPES:
        return []
    if isinstance(sub, str) and any(
        sub.startswith(prefix) for prefix in _IGNORE_SYSTEM_PREFIXES
    ):
        return []
    if sub == "init":
        return [
            SessionStartedEvent(
                model=ev.get("model", ""),
                cwd=ev.get("cwd", ""),
                cc_session_id=ev.get("session_id", ""),
                tools=list(ev.get("tools", [])),
                slash_commands=list(ev.get("slash_commands", [])),
                skills=list(ev.get("skills", [])),
                agents=list(ev.get("agents", [])),
            )
        ]
    if sub == "api_retry":
        # attempt 是上游单次请求内的计数，本地直接透传，不跨 api_retry 消息累加。
        return [
            RetryingEvent(
                attempt=_as_int(ev.get("attempt")) or 0,
                max_retries=_as_int(ev.get("max_retries")),
                error_status=_as_int(ev.get("error_status")),
                error=ev.get("error"),
                retry_delay_ms=_as_int(ev.get("retry_delay_ms")),
            )
        ]
    if sub in ("hook_started", "hook_response"):
        return [
            HookEvent(
                hook_name=ev.get("hook_name", ""),
                outcome=ev.get("outcome"),
            )
        ]
    if sub == "status":
        # 阶段优先取非空 subtype2，其次取非空 stage；两者均为空时标记 unknown。
        stage = ev.get("subtype2") or ev.get("stage") or "unknown"
        return [StatusEvent(stage=stage)]
    if sub == "compact_boundary":
        meta = ev.get("compactMetadata") or {}
        trigger = meta.get("trigger") if isinstance(meta, dict) else None
        return [
            CompactBoundaryEvent(trigger=trigger if isinstance(trigger, str) else None)
        ]
    if sub == "local_command_output":
        return [LocalCommandEvent(content=as_text_fn(ev.get("content")))]
    if sub == "thinking_tokens":
        # thinking_tokens 是思考内容到达前的心跳；estimated_tokens 是截至当前
        # 心跳的累计思考 token 估算值。
        return [
            ThinkingProgressEvent(
                estimated_tokens=int(ev.get("estimated_tokens", 0)),
            )
        ]
    if sub == "task_started":
        return [
            SubagentProgressEvent(
                tool_use_id=ev.get("tool_use_id", ""),
                task_id=ev.get("task_id", ""),
                status="started",
                description=ev.get("description"),
                subagent_type=ev.get("subagent_type"),
            )
        ]
    if sub == "task_progress":
        return [
            SubagentProgressEvent(
                tool_use_id=ev.get("tool_use_id", ""),
                task_id=ev.get("task_id", ""),
                status="progress",
                description=ev.get("description"),
                subagent_type=ev.get("subagent_type"),
                last_tool_name=ev.get("last_tool_name"),
                usage=ev.get("usage"),
            )
        ]
    if sub == "task_notification":
        # 保留上游终态；status 缺失或为空时标记 unknown，不能伪装成成功。
        return [
            SubagentProgressEvent(
                tool_use_id=ev.get("tool_use_id", ""),
                task_id=ev.get("task_id", ""),
                status=ev.get("status") or "unknown",
                usage=ev.get("usage"),
            )
        ]
    logger.debug("unmapped CC system subtype dropped: %r", sub)
    return []


def _as_int(value: Any) -> int | None:
    """将 `api_retry` 的可选计数字段转换为整数。

    布尔值和 `None` 返回 `None`，整数原样返回，有限浮点数由 `int()` 向零截断。
    其他值调用 `int(value)`，仅捕获 `TypeError` 和 `ValueError`；其余异常向上传播。

    Args:
        value: CC 消息中的原始计数值。

    Returns:
        转换后的整数；值为 `None`、布尔值，或其他值转换时触发 `TypeError` 或
        `ValueError`，则返回 `None`。

    Raises:
        ValueError: 浮点数是 `NaN`。
        OverflowError: 浮点数是正无穷或负无穷。
    """

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
