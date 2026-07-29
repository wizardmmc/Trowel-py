"""把需要展示的 Codex 事件转换为 Trowel 通用格式，并按会话连续编号。"""

from __future__ import annotations

from typing import Any, Literal, Mapping

from trowel_py.agent_host.codex_event_mapping import map_codex_event
from trowel_py.codex_host.events import CodexEvent
from trowel_py.agent_host.events import AgentEvent

_CODEX_RUNTIME: Literal["codex"] = "codex"


class CodexEventAdapter:
    """把单个 Codex 会话中需要展示的事件转换为 AgentEvent，并在多个轮次间连续编号。"""

    def __init__(self, session_id: str) -> None:
        """绑定一个 Trowel 会话，并为它单独维护事件序号。"""

        self._session_id = session_id
        self._seq = 0

    @property
    def session_id(self) -> str:
        """返回适配器所属的 Trowel 会话 ID。"""

        return self._session_id

    def wrap(self, event: CodexEvent) -> AgentEvent | None:
        """把 Codex 会话内部事件转换为通用 AgentEvent。

        Args:
            event: 已归属到当前 Trowel 会话的 Codex 内部事件。

        Returns:
            转换后的通用事件；无需向前端展示时返回 None。
        """

        mapped = map_codex_event(event)
        if mapped is None:
            return None
        return self._envelope(
            event,
            type_=mapped.type,
            payload=mapped.payload,
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
            runtime=_CODEX_RUNTIME,
            seq=self._seq,
            type="error",
            payload={"subclass": "host_error", "errors": [str(detail)]},
        )

    def _envelope(
        self,
        event: CodexEvent,
        *,
        type_: str,
        payload: Mapping[str, Any],
    ) -> AgentEvent:
        """为映射后的 Codex 事件补充会话信息和连续序号。

        Args:
            event: 原 Codex 内部事件，用于读取 thread、turn 和 item ID。
            type_: 转换后的通用事件类型。
            payload: 转换后要写入通用事件的内容。
        """

        # Codex 内部序号也会计算未展示的事件，直接使用时前端收到的序号可能从 1 变成 3；
        # 因此这里只为成功映射的事件递增序号。
        self._seq += 1
        return AgentEvent(
            session_id=self._session_id,
            runtime=_CODEX_RUNTIME,
            seq=self._seq,
            type=type_,
            thread_id=_optional_string(event.thread_id),
            turn_id=_optional_string(event.turn_id),
            item_id=_optional_string(event.item_id),
            payload=dict(payload),
        )


def _optional_string(value: Any) -> str | None:
    """只保留字符串值，其他输入统一视为空值。"""

    # TODO(refactor)：可提炼
    return value if isinstance(value, str) else None
