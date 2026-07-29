"""把 Codex 内部事件转换为 Trowel 通用事件类型和内容。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from trowel_py.codex_host.events import CodexEvent, CodexEventType

_COMMAND_TOOL_NAME = "command"
_FILE_CHANGE_KIND = "fileChange"
_MCP_TOOL_KIND = "mcpToolCall"
_APPLY_PATCH_TOOL_NAME = "apply_patch"


@dataclass(frozen=True)
class MappedCodexEvent:
    """保存 Codex 事件转换后的通用事件类型和内容。

    Attributes:
        type: 转换后要写入 AgentEvent.type 的事件类型。
        payload: 转换后要写入 AgentEvent.payload 的事件内容。
    """

    type: str
    payload: Mapping[str, Any]


def _mapped(type_: str, payload: Mapping[str, Any]) -> MappedCodexEvent:
    """创建包含指定事件类型和内容的 Codex 映射结果。"""

    return MappedCodexEvent(type=type_, payload=payload)


def _session_started(event: CodexEvent) -> MappedCodexEvent:
    """将 Codex 的会话启动信息转换为 Trowel 通用格式。"""

    return _mapped(
        "session_started",
        {
            "model": event.payload.get("model"),
            "cwd": event.payload.get("cwd"),
            "cc_session_id": event.thread_id,
            "tools": [],
            "permission_profile": event.payload.get("permission_profile"),
            "effective_sandbox": event.payload.get("effective_sandbox"),
            "effective_approval": event.payload.get("effective_approval"),
            "network_access": event.payload.get("network_access"),
        },
    )


def _model_changed(event: CodexEvent) -> MappedCodexEvent:
    """将 Codex 的模型和思考强度变化转换为 Trowel 通用格式。"""

    return _mapped(
        "model_changed",
        {
            "model": event.payload.get("model"),
            "effort": event.payload.get("effort"),
        },
    )


def _turn_started(event: CodexEvent) -> MappedCodexEvent:
    """将 Codex 的轮次启动信息转换为 Trowel 通用格式，并标记该轮次不能回退。"""

    return _mapped(
        "turn_start",
        {
            "revertible": False,
            "autonomous": event.payload.get("autonomous") is True,
        },
    )


def _user(event: CodexEvent) -> MappedCodexEvent:
    """将 Codex 记录的用户文本转换为 Trowel 通用消息格式。"""

    return _mapped("user", {"text": event.payload.get("text")})


def _assistant_delta(event: CodexEvent) -> MappedCodexEvent:
    """将 Codex 分段返回的助手文本转换为 Trowel 通用正文格式。"""

    return _mapped("text", {"text": event.payload.get("delta")})


def _reasoning_delta(event: CodexEvent) -> MappedCodexEvent:
    """将 Codex 分段返回的思考文本转换为 Trowel 通用思考格式。"""

    return _mapped("thinking", {"text": event.payload.get("delta")})


def _tool_started(event: CodexEvent) -> MappedCodexEvent:
    """根据工具种类，将 Codex 的工具启动信息转换为 Trowel 通用工具调用。"""

    if event.payload.get("kind") == _FILE_CHANGE_KIND:
        return _file_change_started(event)
    if event.payload.get("kind") == _MCP_TOOL_KIND:
        return _mcp_tool_started(event)
    return _mapped(
        "tool_call",
        {
            "tool_use_id": event.item_id,
            "tool_name": _COMMAND_TOOL_NAME,
            "input": {
                "command": event.payload.get("command"),
                "cwd": event.payload.get("cwd"),
                "source": event.payload.get("source"),
                "command_actions": [
                    dict(action)
                    for action in (event.payload.get("command_actions") or ())
                ],
            },
            "started_at_ms": event.payload.get("started_at"),
        },
    )


def _mcp_tool_started(event: CodexEvent) -> MappedCodexEvent:
    """将 Codex 的 MCP 工具启动信息转换为 Trowel 通用工具调用。"""

    return _mapped(
        "tool_call",
        {
            "tool_use_id": event.item_id,
            "tool_name": event.payload.get("tool_name"),
            "input": {
                "server": event.payload.get("server"),
                "tool": event.payload.get("tool"),
                "arguments": event.payload.get("arguments"),
            },
            "started_at_ms": event.payload.get("started_at"),
        },
    )


def _file_change_started(event: CodexEvent) -> MappedCodexEvent:
    """将 Codex 开始修改文件的事件转换为 apply_patch 工具调用。"""

    changes = [dict(change) for change in (event.payload.get("changes") or ())]
    return _mapped(
        "tool_call",
        {
            "tool_use_id": event.item_id,
            "tool_name": _APPLY_PATCH_TOOL_NAME,
            "input": {
                "paths": [change["path"] for change in changes],
                "change_kinds": [change["change_kind"] for change in changes],
            },
            "started_at_ms": event.payload.get("started_at"),
        },
    )


def _tool_completed(event: CodexEvent) -> MappedCodexEvent:
    """按工具种类把 Codex 完成事件整理为共享工具结果。"""

    if event.payload.get("kind") == _FILE_CHANGE_KIND:
        return _file_change_completed(event)
    if event.payload.get("kind") == _MCP_TOOL_KIND:
        return _mcp_tool_completed(event)
    return _mapped(
        "tool_result",
        {
            "tool_use_id": event.item_id,
            "content": event.payload.get("output"),
            "exit_code": event.payload.get("exit_code"),
            "duration_ms": event.payload.get("duration_ms"),
            "cwd": event.payload.get("cwd"),
            "command": event.payload.get("command"),
            "status": event.payload.get("status"),
        },
    )


def _mcp_tool_completed(event: CodexEvent) -> MappedCodexEvent:
    """把 MCP 工具完成事实整理为共享工具结果。"""

    error = event.payload.get("error")
    content = (
        _mcp_error_content(error)
        if error is not None
        else _tool_content(event.payload.get("result"))
    )
    return _mapped(
        "tool_result",
        {
            "tool_use_id": event.item_id,
            "tool_name": event.payload.get("tool_name"),
            "content": content,
            "status": event.payload.get("status"),
            "duration_ms": event.payload.get("duration_ms"),
        },
    )


def _mcp_error_content(value: Any) -> str | None:
    """优先提取 MCP 错误消息，再回退为可展示文本。"""

    if isinstance(value, Mapping):
        message = value.get("message")
        if isinstance(message, str):
            return message
    return _tool_content(value)


def _tool_content(value: Any) -> str | None:
    """把工具返回值转换为稳定的可展示文本。"""

    if value is None or isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def _file_change_completed(event: CodexEvent) -> MappedCodexEvent:
    """把文件变更完成事实整理为 apply_patch 工具结果。"""

    changes = [dict(change) for change in (event.payload.get("changes") or ())]
    # 首项字段兼容旧展示契约；完整列表保留原生批量语义。
    first = changes[0] if changes else {}
    return _mapped(
        "tool_result",
        {
            "tool_use_id": event.item_id,
            "tool_name": _APPLY_PATCH_TOOL_NAME,
            "content": None,
            "change_kind": first.get("change_kind"),
            "path": first.get("path"),
            "move_path": first.get("move_path"),
            "write_diff": first.get("write_diff"),
            "changes": changes,
            "status": event.payload.get("status"),
            "completed_at_ms": event.payload.get("completed_at"),
        },
    )


def _passthrough(event: CodexEvent) -> MappedCodexEvent:
    """把 Codex 内部事件复制为映射结果，不改变事件类型和内容。"""

    return _mapped(event.type.value, dict(event.payload))


def _compaction(event: CodexEvent) -> MappedCodexEvent:
    """将 Codex 已完成的上下文压缩转换为 Trowel 通用格式。"""

    payload = dict(event.payload)
    # Codex 只在压缩完成后产生这个事件，因此直接标记为 "completed"，
    # 前端和 Model OS 无需再次判断压缩是否完成。
    payload["phase"] = "completed"
    return _mapped("compaction", payload)


def _review_mode(event: CodexEvent) -> MappedCodexEvent:
    """将 Codex 进入或退出代码审查模式的信息转换为前端提示消息。"""

    phase = event.payload.get("phase")
    label = "开始代码审查" if phase == "entered" else "代码审查结束"
    review = event.payload.get("review")
    content = f"{label}：{review}" if isinstance(review, str) and review else label
    return _mapped("local_command", {"content": content})


def _status(event: CodexEvent) -> MappedCodexEvent:
    """把 Codex 状态和活动标记整理为共享状态事件。"""

    return _mapped(
        "status",
        {
            "stage": event.payload.get("status"),
            "active_flags": list(event.payload.get("active_flags") or ()),
        },
    )


def _finished(event: CodexEvent) -> MappedCodexEvent:
    """将 Codex 当前状态和等待、暂停原因转换为 Trowel 通用状态格式。"""

    return _mapped(
        "finished",
        {
            "usage": None,
            "total_cost_usd": None,
            "num_turns": None,
            "duration_ms": event.payload.get("duration_ms"),
        },
    )


def _interrupted(_: CodexEvent) -> MappedCodexEvent:
    """将 Codex 轮次已被中断的信息转换为 Trowel 通用格式。"""

    return _mapped("interrupted", {})


def _error(event: CodexEvent) -> MappedCodexEvent:
    """将 Codex 错误转换为重试提示或轮次失败事件。"""

    # Codex 的 error 通知只是轮次中的错误消息，不代表轮次已经结束；
    # 只有状态为 "failed" 的 turn/completed 才表示轮次最终失败。
    if event.payload.get("kind") == "native_error":
        return _mapped(
            "retrying",
            {
                "attempt": 1,
                "max_retries": None,
                "error_status": None,
                "error": event.payload.get("message"),
                "retry_delay_ms": None,
            },
        )

    error = event.payload.get("error")
    message = (
        error
        if isinstance(error, str)
        else error.get("message")
        if isinstance(error, dict)
        else None
    )
    return _mapped(
        "error",
        {
            "subclass": "turn_failed",
            "errors": [message] if isinstance(message, str) and message else [],
            "api_error_status": None,
        },
    )


def _drop(_: CodexEvent) -> None:
    """忽略不需要对外发送的 Codex 内部事件。"""

    return None


Mapper = Callable[[CodexEvent], MappedCodexEvent | None]

_MAPPERS: dict[CodexEventType, Mapper] = {
    CodexEventType.SESSION_STARTED: _session_started,
    CodexEventType.MODEL_CHANGED: _model_changed,
    CodexEventType.TURN_STARTED: _turn_started,
    CodexEventType.USER: _user,
    CodexEventType.ASSISTANT_DELTA: _assistant_delta,
    # 助手正文已由分段事件累积完成，再发送完整消息会让前端重复显示。
    CodexEventType.ASSISTANT_MESSAGE: _drop,
    CodexEventType.REASONING_DELTA: _reasoning_delta,
    CodexEventType.TOOL_STARTED: _tool_started,
    # 当前没有经过验证的工具进度事件来源，不能自行补造进度和耗时。
    CodexEventType.TOOL_PROGRESS: _drop,
    CodexEventType.TOOL_COMPLETED: _tool_completed,
    CodexEventType.APPROVAL_REQUEST: _passthrough,
    CodexEventType.USAGE_UPDATED: _passthrough,
    CodexEventType.RATE_LIMIT_UPDATED: _passthrough,
    CodexEventType.GOAL_UPDATED: _passthrough,
    CodexEventType.GOAL_CLEARED: _passthrough,
    CodexEventType.PLAN_UPDATED: _passthrough,
    CodexEventType.TURN_DIFF_UPDATED: _passthrough,
    CodexEventType.REVIEW_MODE: _review_mode,
    CodexEventType.STATUS: _status,
    CodexEventType.FINISHED: _finished,
    CodexEventType.INTERRUPTED: _interrupted,
    CodexEventType.ERROR: _error,
    CodexEventType.HOST_STATUS: _passthrough,
    CodexEventType.COMPACTION: _compaction,
    CodexEventType.SUBAGENT_ACTIVITY: _passthrough,
}


def map_codex_event(event: CodexEvent) -> MappedCodexEvent | None:
    """根据 Codex 内部事件类型选择对应的转换方法。

    Args:
        event: 需要转换的 Codex 内部事件。

    Returns:
        转换后的通用事件内容；没有对应规则时返回 None。
    """

    mapper = _MAPPERS.get(event.type)
    # 没有映射规则说明该事件格式尚未验证，不能根据字段猜测处理方式。
    return mapper(event) if mapper is not None else None
