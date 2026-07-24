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
        # task_updated 与 notification 重复且缺少关联 ID，不单独映射。
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
        # attempt 属于上游单次请求，禁止在本地跨重试轮次累加。
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
        # 阶段字段兼容 subtype2 与 stage，均缺失时显式标记 unknown。
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
        # thinking_tokens 是正文到达前维持思考状态的心跳。
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
        # 原样传递终态；字段缺失时标记 unknown，不能伪装成成功。
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
    # bool 不是有效计数；上游小数在事件边界截断，避免 schema 拒绝。
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
