"""把 Claude Code 事件包装成 Trowel 通用格式，并按会话连续编号。"""

from __future__ import annotations

from typing import Any

from trowel_py.agent_host.events import AgentEvent

# tool_use_id 同时写入 AgentEvent.item_id，用于关联工具调用及其结果；原字段继续保留在事件正文中。
_ITEM_ID_FIELDS: tuple[str, ...] = ("tool_use_id",)


def _coerce_optional_str(value: Any) -> str | None:
    """原样返回字符串，其他类型返回 None。"""

    # TODO(refactor)：可提炼
    return value if isinstance(value, str) else None


def _shallow_copy_minus_type(event: dict[str, Any]) -> dict[str, Any]:
    """复制事件最外层的字典，并移除已写入 AgentEvent.type 的 type 字段。"""

    return {k: v for k, v in event.items() if k != "type"}


class CcEventAdapter:
    """把单个 Claude Code 会话的事件转换为 AgentEvent，并在多个轮次间连续编号。"""

    def __init__(self, session_id: str) -> None:
        """绑定一个 Trowel 会话，并为它单独维护事件序号。"""

        self._session_id = session_id
        self._seq = 0

    @property
    def session_id(self) -> str:
        """返回适配器所属的 Trowel 会话 ID。"""

        return self._session_id

    def wrap(self, event: dict[str, Any]) -> AgentEvent:
        """把已翻译的 Claude Code 事件转换为当前会话的 AgentEvent。

        Args:
            event: Claude Code 翻译层生成的事件字典。

        Raises:
            KeyError: 事件缺少 type 字段。
        """

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
        """为当前会话生成带连续序号的错误事件。

        连续序号保证前端不会把它当成重复事件丢弃。

        Args:
            detail: 异常对象或错误说明，写入事件时转换为字符串。
        """

        self._seq += 1
        return AgentEvent(
            session_id=self._session_id,
            runtime="claude_code",
            seq=self._seq,
            type="error",
            payload={"subclass": "host_error", "errors": [str(detail)]},
        )


def _item_id_from_event(event: dict[str, Any]) -> str | None:
    """提取 Claude Code 事件中的工具调用 ID，用于关联调用和对应结果。"""

    for field in _ITEM_ID_FIELDS:
        value = event.get(field)
        if isinstance(value, str):
            return value
    return None
