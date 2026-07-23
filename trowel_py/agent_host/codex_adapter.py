"""为 Codex 事件分配共享 envelope 与连续会话序列。"""

from __future__ import annotations

from typing import Any, Literal, Mapping

from trowel_py.agent_host.codex_event_mapping import map_codex_event
from trowel_py.codex_host.events import CodexEvent
from trowel_py.schemas.agent_host import AgentEvent

_CODEX_RUNTIME: Literal["codex"] = "codex"


class CodexEventAdapter:
    """维护单个 Codex 会话的 AgentEvent 序列。"""

    def __init__(self, session_id: str) -> None:
        self._session_id = session_id
        self._seq = 0

    @property
    def session_id(self) -> str:
        return self._session_id

    def wrap(self, event: CodexEvent) -> AgentEvent | None:
        mapped = map_codex_event(event)
        if mapped is None:
            return None
        return self._envelope(
            event,
            type_=mapped.type,
            payload=mapped.payload,
        )

    def error_event(self, detail: Any) -> AgentEvent:
        """让 route 错误沿用本会话的序列，避免被前端误判为重复事件。"""

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
        # 使用独立序列，丢弃的原生事件不会让前端观察到空洞。
        self._seq += 1
        return AgentEvent(
            session_id=self._session_id,
            runtime=_CODEX_RUNTIME,
            seq=self._seq,
            type=type_,
            turn_id=_optional_string(event.turn_id),
            item_id=_optional_string(event.item_id),
            payload=dict(payload),
        )


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) else None
