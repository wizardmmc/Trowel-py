"""定义 Codex host 的内部事件边界。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

# 共享只读空映射，避免为无 payload 的事件重复分配字典。
_EMPTY_PAYLOAD: Mapping[str, Any] = MappingProxyType({})


class CodexEventType(str, Enum):
    """Codex translator 与 manager 使用的内部事件判别符。"""

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
    PLAN_UPDATED = "plan_updated"
    SUBAGENT_ACTIVITY = "subagent_activity"
    COMPACTION = "compaction"
    HOST_WARNING = "host_warning"
    HOST_STATUS = "host_status"


class HostStatusKind(str, Enum):
    """前端可见的 host 状态，与 manager 内部生命周期状态分离。"""

    READY = "ready"
    DEGRADED = "degraded"
    HOST_EXITED = "host_exited"
    # 预留状态；当前没有发射点。
    RESTARTING = "restarting"


@dataclass(frozen=True)
class TranslatedItem:
    """尚未绑定 trowel 会话和序号的翻译结果。

    manager 按 thread_id 路由，所属 session 再补 session_id 和单会话 seq。
    """

    type: CodexEventType
    thread_id: str | None = None
    turn_id: str | None = None
    item_id: str | None = None
    payload: Mapping[str, Any] = _EMPTY_PAYLOAD


@dataclass(frozen=True)
class CodexEvent:
    """已由 session 盖章的内部事件；seq 只在所属会话内单调。"""

    session_id: str
    seq: int
    type: CodexEventType
    thread_id: str | None = None
    turn_id: str | None = None
    item_id: str | None = None
    payload: Mapping[str, Any] = field(default=_EMPTY_PAYLOAD)

    def as_dict(self) -> dict[str, Any]:
        """返回事件字典；payload 只复制顶层映射。"""

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
    """冻结 payload 的顶层键映射，不复制或冻结嵌套值。"""

    return MappingProxyType(dict(fields))


def host_status_item(
    status: HostStatusKind,
    *,
    thread_id: str | None = None,
    reason: str | None = None,
    exit_code: int | None = None,
) -> TranslatedItem:
    """把 manager/transport 状态合成为 HOST_STATUS 中间事件。"""

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
