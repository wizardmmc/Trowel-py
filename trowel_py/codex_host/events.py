"""定义 Codex Host 在 Trowel 内部传递事件时使用的数据模型。

``TranslatedItem`` 表示尚未绑定 Trowel 会话和序号的中间事件，可由 translator、
manager 或 session 创建。实时事件经 ``CodexSession`` 补充会话 ID 和递增序号后，
形成 ``CodexEvent``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

# 无字段事件复用同一个不可变 payload，避免重复分配空字典。
_EMPTY_PAYLOAD: Mapping[str, Any] = MappingProxyType({})


class CodexEventType(str, Enum):
    """区分 Codex Host 内部事件的稳定判别符。"""

    SESSION_STARTED = "session_started"
    MODEL_CHANGED = "model_changed"
    TURN_STARTED = "turn_started"
    # turn/start 接受后由 session 在本地回显。
    USER = "user"
    ASSISTANT_DELTA = "assistant_delta"
    ASSISTANT_MESSAGE = "assistant_message"
    REASONING_DELTA = "reasoning_delta"
    TOOL_STARTED = "tool_started"
    TOOL_PROGRESS = "tool_progress"
    TOOL_COMPLETED = "tool_completed"
    # server request 由 manager 合成，不来自 translator 通知。
    APPROVAL_REQUEST = "approval_request"
    USAGE_UPDATED = "usage_updated"
    STATUS = "status"
    FINISHED = "finished"
    INTERRUPTED = "interrupted"
    ERROR = "error"
    # 账户级通知没有 thread_id，由 manager 向全部已注册 session 广播。
    RATE_LIMIT_UPDATED = "rate_limit_updated"
    GOAL_UPDATED = "goal_updated"
    GOAL_CLEARED = "goal_cleared"
    PLAN_UPDATED = "plan_updated"
    TURN_DIFF_UPDATED = "turn_diff_updated"
    REVIEW_MODE = "review_mode"
    SUBAGENT_ACTIVITY = "subagent_activity"
    COMPACTION = "compaction"
    HOST_WARNING = "host_warning"
    HOST_STATUS = "host_status"


class HostStatusKind(str, Enum):
    """可发送到前端的 Host 状态，不表示 manager 的内部生命周期。"""

    READY = "ready"
    DEGRADED = "degraded"
    HOST_EXITED = "host_exited"
    # 预留状态；当前没有发射点。
    RESTARTING = "restarting"


@dataclass(frozen=True)
class TranslatedItem:
    """尚未绑定 Trowel 会话和序号的内部中间事件。

    Attributes:
        type: 内部事件类型。
        thread_id: Codex thread ID；账户级通知等无所属 thread 的事件为 None。
        turn_id: Codex turn ID；不属于某个 turn 的事件为 None。
        item_id: Codex item ID；非 item 事件为 None。
        payload: 事件字段的只读顶层映射；嵌套值不保证不可变。
    """

    type: CodexEventType
    thread_id: str | None = None
    turn_id: str | None = None
    item_id: str | None = None
    payload: Mapping[str, Any] = _EMPTY_PAYLOAD


@dataclass(frozen=True)
class CodexEvent:
    """已绑定 Trowel 会话并分配序号的内部事件。

    Attributes:
        session_id: 接收该事件的 Trowel 会话 ID。
        seq: 所属会话内单调递增的事件序号。
        type: 内部事件类型。
        thread_id: Codex thread ID；事件未绑定 thread 时为 None。
        turn_id: Codex turn ID；事件不属于某个 turn 时为 None。
        item_id: Codex item ID；非 item 事件为 None。
        payload: 事件字段的只读顶层映射；嵌套值不保证不可变。
    """

    session_id: str
    seq: int
    type: CodexEventType
    thread_id: str | None = None
    turn_id: str | None = None
    item_id: str | None = None
    payload: Mapping[str, Any] = field(default=_EMPTY_PAYLOAD)

    def as_dict(self) -> dict[str, Any]:
        """按 ``codex-event-v1`` schema 序列化事件，并浅复制 payload。"""

        return {
            "schema": "codex-event-v1",
            "session_id": self.session_id,
            "runtime": "codex",
            "seq": self.seq,
            "type": self.type.value,
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
            "item_id": self.item_id,
            "payload": dict(self.payload),
        }


def immutable_payload(**fields: Any) -> Mapping[str, Any]:
    """将事件字段复制为只读顶层映射；嵌套值保持原引用。"""

    return MappingProxyType(dict(fields))


def host_status_item(
    status: HostStatusKind,
    *,
    thread_id: str | None = None,
    reason: str | None = None,
    exit_code: int | None = None,
) -> TranslatedItem:
    """创建 Host 状态中间事件，省略值为 None 的可选诊断字段。

    Args:
        status: 对外发送的 Host 状态。
        thread_id: 关联的 Codex thread ID；状态不关联特定 thread 时为 None。
        reason: 状态原因；未知或无需说明时为 None。
        exit_code: app-server 退出码；进程未退出或退出码未知时为 None。

    Returns:
        等待 session 分配会话 ID 和序号的 ``HOST_STATUS`` 事件。
    """

    payload_fields: dict[str, Any] = {"status": status.value}
    if reason is not None:
        payload_fields["reason"] = reason
    if exit_code is not None:
        payload_fields["exit_code"] = exit_code
    return TranslatedItem(
        type=CodexEventType.HOST_STATUS,
        thread_id=thread_id,
        payload=immutable_payload(**payload_fields),
    )
