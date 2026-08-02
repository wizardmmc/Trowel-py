"""把 Codex 会话状态和事件接入 Agent Host 的统一边界。"""

from __future__ import annotations

import logging
from typing import Any, Literal, Mapping

from trowel_py.agent_host.binding import Runtime, SessionBinding
from trowel_py.agent_host.codex_event_mapping import map_codex_event
from trowel_py.agent_host.events import AgentEvent
from trowel_py.agent_host.runtimes.base import RuntimeCloseResult, RuntimeLiveState
from trowel_py.codex_host.events import CodexEvent
from trowel_py.codex_host.errors import CodexHostError

_CODEX_RUNTIME: Literal["codex"] = "codex"
_log = logging.getLogger(__name__)


class CodexRuntimeAdapter:
    """向 Agent Host 公开 Codex 会话的共同状态和关闭操作。"""

    runtime = Runtime.CODEX

    def __init__(self, manager: Any | None) -> None:
        """绑定可能尚未启用的 Codex 进程和 thread 管理器。

        Args:
            manager: Codex 会话管理器；None 表示当前应用未启用 Codex。
        """

        self._manager = manager

    def session_ids(self) -> tuple[str, ...]:
        """返回当前 manager 登记的全部 Trowel 会话 ID。"""

        if self._manager is None:
            return ()
        return tuple(self._manager.session_ids)

    def live_state(self, session_id: str) -> RuntimeLiveState:
        """读取 CodexSession 明确公开的未结束轮次状态。"""

        if self._manager is None:
            return RuntimeLiveState.disconnected()
        session = self._manager.get_session(session_id)
        if session is None:
            return RuntimeLiveState.disconnected()
        return RuntimeLiveState(
            connected=True,
            has_in_flight_turn=bool(session.has_in_flight_turn),
        )

    async def close(self, binding: SessionBinding) -> RuntimeCloseResult:
        """先收敛 Codex thread 资源，再从 manager 注销本地路由。

        用户持久 thread 在 archive 后恢复历史可见性；委派和其他内部 thread 保持
        archived。任一原生操作失败都会保留 manager 登记和持久 binding 供重试。

        Args:
            binding: 提供 session 类别和 Trowel 会话 ID 的持久记录。
        """

        if self._manager is None:
            return RuntimeCloseResult.closed()
        session = self._manager.get_session(binding.session_id)
        if session is None:
            return RuntimeCloseResult.closed()
        try:
            await self._manager.close_session(
                session,
                preserve_history=binding.session_kind == "user",
            )
        except (CodexHostError, RuntimeError) as exc:
            _log.warning(
                "Codex session close needs reconciliation: %s",
                type(exc).__name__,
            )
            return RuntimeCloseResult.needs_reconcile(
                remaining_resource_count=1,
                remaining_resource_kinds=("codex_session_close",),
                error=f"Codex close needs reconciliation: {type(exc).__name__}",
            )
        self._manager.unregister(binding.session_id)
        return RuntimeCloseResult.closed()

    def abort_create(self, session_id: str) -> None:
        """撤销尚未提交 binding 的 Codex manager 登记。"""

        if self._manager is not None:
            self._manager.unregister(session_id)


class CodexEventAdapter:
    """把单个 Codex 会话中需要展示的事件转换为连续编号的 AgentEvent。"""

    def __init__(self, session_id: str) -> None:
        """绑定一个 Trowel 会话，并为它单独维护事件序号。"""

        self._session_id = session_id
        self._seq = 0

    @property
    def session_id(self) -> str:
        """返回适配器所属的 Trowel 会话 ID。"""

        return self._session_id

    def wrap(self, event: CodexEvent) -> AgentEvent | None:
        """转换一条 Codex 内部事件；无需展示时返回 None。"""

        mapped = map_codex_event(event)
        if mapped is None:
            return None
        return self._envelope(
            event,
            type_=mapped.type,
            payload=mapped.payload,
        )

    def error_event(self, detail: Any) -> AgentEvent:
        """生成与普通事件共用连续序号的 host 错误事件。"""

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
        """补充会话身份，并只为实际展示的 Codex 事件递增序号。"""

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

    return value if isinstance(value, str) else None
