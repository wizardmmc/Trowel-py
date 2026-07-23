"""将 Codex 原生事件映射到共享 AgentEvent 词汇。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from trowel_py.codex_host.events import CodexEvent, CodexEventType

_COMMAND_TOOL_NAME = "command"
_FILE_CHANGE_KIND = "fileChange"
_APPLY_PATCH_TOOL_NAME = "apply_patch"


@dataclass(frozen=True)
class MappedCodexEvent:
    type: str
    payload: Mapping[str, Any]


def _mapped(type_: str, payload: Mapping[str, Any]) -> MappedCodexEvent:
    return MappedCodexEvent(type=type_, payload=payload)


def _session_started(event: CodexEvent) -> MappedCodexEvent:
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
    return _mapped(
        "model_changed",
        {
            "model": event.payload.get("model"),
            "effort": event.payload.get("effort"),
        },
    )


def _turn_started(_: CodexEvent) -> MappedCodexEvent:
    return _mapped("turn_start", {"revertible": False})


def _user(event: CodexEvent) -> MappedCodexEvent:
    return _mapped("user", {"text": event.payload.get("text")})


def _assistant_delta(event: CodexEvent) -> MappedCodexEvent:
    return _mapped("text", {"text": event.payload.get("delta")})


def _reasoning_delta(event: CodexEvent) -> MappedCodexEvent:
    return _mapped("thinking", {"text": event.payload.get("delta")})


def _tool_started(event: CodexEvent) -> MappedCodexEvent:
    if event.payload.get("kind") == _FILE_CHANGE_KIND:
        return _file_change_started(event)
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


def _file_change_started(event: CodexEvent) -> MappedCodexEvent:
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
    if event.payload.get("kind") == _FILE_CHANGE_KIND:
        return _file_change_completed(event)
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


def _file_change_completed(event: CodexEvent) -> MappedCodexEvent:
    changes = [dict(change) for change in (event.payload.get("changes") or ())]
    # 当前协议每个 item 对应一处文件变更，同时保留完整列表以兼容批量形态。
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
    return _mapped(event.type.value, dict(event.payload))


def _compaction(event: CodexEvent) -> MappedCodexEvent:
    payload = dict(event.payload)
    # translator 只转发完成事件；显式字段避免下游猜测阶段。
    payload["phase"] = "completed"
    return _mapped("compaction", payload)


def _status(event: CodexEvent) -> MappedCodexEvent:
    return _mapped(
        "status",
        {
            "stage": event.payload.get("status"),
            "active_flags": list(event.payload.get("active_flags") or ()),
        },
    )


def _finished(event: CodexEvent) -> MappedCodexEvent:
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
    return _mapped("interrupted", {})


def _error(event: CodexEvent) -> MappedCodexEvent:
    # 原生 error 通知不结束 turn；只有失败的 turn/completed 才是终态。
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
    return None


Mapper = Callable[[CodexEvent], MappedCodexEvent | None]

_MAPPERS: dict[CodexEventType, Mapper] = {
    CodexEventType.SESSION_STARTED: _session_started,
    CodexEventType.MODEL_CHANGED: _model_changed,
    CodexEventType.TURN_STARTED: _turn_started,
    CodexEventType.USER: _user,
    CodexEventType.ASSISTANT_DELTA: _assistant_delta,
    # 最终消息已由 delta 累积，继续下发会让共享 reducer 重复正文。
    CodexEventType.ASSISTANT_MESSAGE: _drop,
    CodexEventType.REASONING_DELTA: _reasoning_delta,
    CodexEventType.TOOL_STARTED: _tool_started,
    # 当前 translator 没有可靠的进度生产者，不能凭空构造耗时。
    CodexEventType.TOOL_PROGRESS: _drop,
    CodexEventType.TOOL_COMPLETED: _tool_completed,
    CodexEventType.APPROVAL_REQUEST: _passthrough,
    CodexEventType.USAGE_UPDATED: _passthrough,
    CodexEventType.RATE_LIMIT_UPDATED: _passthrough,
    CodexEventType.STATUS: _status,
    CodexEventType.FINISHED: _finished,
    CodexEventType.INTERRUPTED: _interrupted,
    CodexEventType.ERROR: _error,
    CodexEventType.HOST_STATUS: _passthrough,
    CodexEventType.COMPACTION: _compaction,
}


def map_codex_event(event: CodexEvent) -> MappedCodexEvent | None:
    mapper = _MAPPERS.get(event.type)
    # 未启用的能力没有稳定 wire shape，缺少映射时宁可丢弃。
    return mapper(event) if mapper is not None else None
