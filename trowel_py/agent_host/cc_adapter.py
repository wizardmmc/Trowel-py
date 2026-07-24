"""保留 CC payload 语义，并添加共享 envelope 与连续会话序列。"""

from __future__ import annotations

from typing import Any

from trowel_py.agent_host.events import AgentEvent

# ``tool_use_id`` 提升为 item_id 供生命周期关联，同时保留在 payload 中兼容 reducer。
_ITEM_ID_FIELDS: tuple[str, ...] = ("tool_use_id",)


def _coerce_optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _shallow_copy_minus_type(event: dict[str, Any]) -> dict[str, Any]:
    """只隔离顶层状态；``type`` 已提升到 envelope，不在 payload 中重复。"""

    return {k: v for k, v in event.items() if k != "type"}


class CcEventAdapter:
    """维护单个 CC 会话跨 turn 连续的 AgentEvent 序列。"""

    def __init__(self, session_id: str) -> None:
        self._session_id = session_id
        self._seq = 0

    @property
    def session_id(self) -> str:
        return self._session_id

    def wrap(self, event: dict[str, Any]) -> AgentEvent:
        """包装一个事件；缺少 ``type`` 时保持 KeyError，不猜测事件类型。"""

        self._seq += 1
        event_type = event["type"]
        return AgentEvent(
            session_id=self._session_id,
            runtime="claude_code",
            seq=self._seq,
            type=event_type,
            turn_id=_coerce_optional_str(event.get("turn_id")),
            item_id=_item_id_from_event(event),
            payload=_shallow_copy_minus_type(event),
        )

    def error_event(self, detail: Any) -> AgentEvent:
        """从同一会话序列生成终止错误，避免前端去重时丢弃错误帧。"""

        self._seq += 1
        return AgentEvent(
            session_id=self._session_id,
            runtime="claude_code",
            seq=self._seq,
            type="error",
            payload={"subclass": "host_error", "errors": [str(detail)]},
        )


def _item_id_from_event(event: dict[str, Any]) -> str | None:
    for field in _ITEM_ID_FIELDS:
        value = event.get(field)
        if isinstance(value, str):
            return value
    return None
