"""把 Claude Code 会话状态和事件接入 Agent Host 的统一边界。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from trowel_py.agent_host.binding import Runtime, SessionBinding
from trowel_py.agent_host.events import AgentEvent
from trowel_py.agent_host.runtimes.base import RuntimeCloseResult, RuntimeLiveState

CcCloser = Callable[[str, dict[str, Any]], Awaitable[None]]
CcCreateAborter = Callable[[str, dict[str, Any]], None]

_ITEM_ID_FIELDS: tuple[str, ...] = ("tool_use_id",)
_TURN_TERMINAL_TYPES: frozenset[str] = frozenset(
    {"finished", "interrupted", "error"}
)


async def _default_closer(session_id: str, registry: dict[str, Any]) -> None:
    """复用 Claude Code 路由的关闭流程清理共享 registry。"""

    from trowel_py.cc_host import routes as cc_routes

    await cc_routes.close_cc_session(session_id, registry)


def _default_create_aborter(session_id: str, registry: dict[str, Any]) -> None:
    """撤销尚未启动原生进程的 Claude Code 会话。"""

    from trowel_py.cc_host import routes as cc_routes

    cc_routes.discard_unstarted_cc_session(session_id, registry)


class ClaudeCodeRuntimeAdapter:
    """向 Agent Host 公开 Claude Code 会话的共同状态和关闭操作。"""

    runtime = Runtime.CLAUDE_CODE

    def __init__(
        self,
        registry: dict[str, Any],
        *,
        closer: CcCloser | None = None,
        create_aborter: CcCreateAborter | None = None,
    ) -> None:
        """绑定 Claude Code 共享 registry 和对应的关闭入口。

        Args:
            registry: Trowel 会话 ID 到实时 CCHost 的共享映射。
            closer: 关闭 CCHost 并同步清理 registry 的异步函数。
            create_aborter: 同步撤销尚未启动的 CCHost 创建并清理 registry 的函数。
        """

        self._registry = registry
        self._closer = closer or _default_closer
        self._create_aborter = create_aborter or _default_create_aborter

    def session_ids(self) -> tuple[str, ...]:
        """按登记顺序返回全部 Claude Code 会话 ID。"""

        return tuple(self._registry)

    def live_state(self, session_id: str) -> RuntimeLiveState:
        """读取逻辑会话登记和未结束轮次状态。

        Claude Code 子进程在主动中断后会退出，但 CCHost 仍保留原生会话 ID，下一条
        消息会按需启动新进程并恢复上下文。因此 ``connected`` 表示逻辑会话仍登记，
        不能等同于当前子进程是否存活；只有显式关闭移除 registry 才算断开。
        """

        host = self._registry.get(session_id)
        if host is None:
            return RuntimeLiveState.disconnected()
        return RuntimeLiveState(
            connected=True,
            has_in_flight_turn=bool(host.has_in_flight_turn),
        )

    async def close(self, binding: SessionBinding) -> RuntimeCloseResult:
        """关闭 Claude Code 进程组，并在成功后移除实时会话登记。

        Args:
            binding: 提供 Trowel 会话 ID 和已冻结 runtime 语义的持久记录。
        """

        try:
            await self._closer(binding.session_id, self._registry)
        except RuntimeError as exc:
            return RuntimeCloseResult.needs_reconcile(
                remaining_resource_count=1,
                remaining_resource_kinds=("claude_code_process_group",),
                error=f"Claude Code close needs reconciliation: {type(exc).__name__}",
            )
        return RuntimeCloseResult.closed()

    def abort_create(self, session_id: str) -> None:
        """撤销尚未提交 binding、也尚未启动子进程的 Claude Code 会话。"""

        self._create_aborter(session_id, self._registry)


def _coerce_optional_str(value: Any) -> str | None:
    """原样返回字符串，其他类型返回 None。"""

    return value if isinstance(value, str) else None


def _shallow_copy_minus_type(event: dict[str, Any]) -> dict[str, Any]:
    """复制事件最外层字典，并移除已写入 AgentEvent.type 的 type 字段。"""

    return {k: v for k, v in event.items() if k != "type"}


class ClaudeCodeEventAdapter:
    """把单个 Claude Code 会话的事件转换为连续编号的 AgentEvent。"""

    def __init__(self, session_id: str) -> None:
        """绑定一个 Trowel 会话，并为它单独维护事件序号。"""

        self._session_id = session_id
        self._seq = 0
        self._current_turn_id: str | None = None

    @property
    def session_id(self) -> str:
        """返回适配器所属的 Trowel 会话 ID。"""

        return self._session_id

    def begin_turn(self, turn_id: str) -> None:
        """在原生首帧到达前绑定 Agent Host 已接受的逻辑 turn。"""

        self._current_turn_id = turn_id

    def wrap(self, event: dict[str, Any]) -> AgentEvent:
        """把 Claude Code 翻译层生成的事件包装为 AgentEvent。"""

        self._seq += 1
        event_type = event["type"]
        explicit_turn_id = _coerce_optional_str(event.get("turn_id"))
        if event_type == "turn_start" and self._current_turn_id is None:
            self._current_turn_id = explicit_turn_id
        turn_id = self._current_turn_id or explicit_turn_id
        envelope = AgentEvent(
            session_id=self._session_id,
            runtime="claude_code",
            seq=self._seq,
            type=event_type,
            turn_id=turn_id,
            item_id=_item_id_from_event(event),
            payload=_shallow_copy_minus_type(event),
        )
        if event_type in _TURN_TERMINAL_TYPES:
            self._current_turn_id = None
        return envelope

    def error_event(self, detail: Any) -> AgentEvent:
        """生成与普通事件共用连续序号的 host 错误事件。"""

        self._seq += 1
        envelope = AgentEvent(
            session_id=self._session_id,
            runtime="claude_code",
            seq=self._seq,
            type="error",
            turn_id=self._current_turn_id,
            payload={"subclass": "host_error", "errors": [str(detail)]},
        )
        self._current_turn_id = None
        return envelope


def _item_id_from_event(event: dict[str, Any]) -> str | None:
    """提取用于关联工具调用及其结果的 Claude Code 工具调用 ID。"""

    for field in _ITEM_ID_FIELDS:
        value = event.get(field)
        if isinstance(value, str):
            return value
    return None
