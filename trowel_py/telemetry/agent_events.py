"""把统一 Agent 事件压缩成不含正文的 turn 与工具调用 span。"""

from __future__ import annotations

import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from trowel_py.telemetry.events import (
    TraceContext,
    create_span_context,
    emit_span,
    trace_link,
)
from trowel_py.telemetry.port import TelemetryPort

_TERMINAL_TYPES = frozenset({"finished", "interrupted", "error", "session_exited"})
_ERROR_STATUSES = frozenset({"failed", "error", "cancelled", "interrupted"})


@dataclass(frozen=True)
class _PreparedTurn:
    """保存 HTTP 请求与尚未收到 turn_start 的 runtime 调用关联。

    Attributes:
        runtime: 当前会话使用的原生 runtime。
        prepared_at: Trowel 开始请求 runtime 的墙钟时刻。
        context: 预先分配的 Agent Host turn 上下文；当前 HTTP span 存在时
            保留真实父子关系。
    """

    runtime: Literal["claude_code", "codex"]
    prepared_at: datetime
    context: TraceContext


@dataclass(frozen=True)
class _ActiveTurn:
    """记录正在执行的 Agent turn 及其可供工具 span 关联的上下文。

    Attributes:
        runtime: 当前会话使用的原生 runtime。
        turn_id: runtime 报告的轮次 ID；Claude Code 缺失时为 None。
        started_at: Trowel 开始向 runtime 提交 turn 的墙钟时刻。
        context: Agent Host turn 的真实进程内上下文。
        runtime_started_at: 原生 runtime 报告 turn_start 的墙钟时刻。
        runtime_context: 不能冒充 Agent Host 子 span 的 runtime 黑盒上下文。
    """

    runtime: Literal["claude_code", "codex"]
    turn_id: str | None
    started_at: datetime
    context: TraceContext
    runtime_started_at: datetime
    runtime_context: TraceContext


@dataclass(frozen=True)
class _ActiveTool:
    """保存一条工具调用完成前允许进入遥测的最小状态。

    Attributes:
        runtime: 产生工具事件的原生 runtime。
        operation: 普通 runtime 工具或 MCP 调用的受控 operation。
        component: runtime 或 mcp 组件。
        started_at: 工具启动事件到达 Trowel 的墙钟时刻。
        context: 工具自己的独立 trace/span 上下文。
        runtime_context: 能核查到所属 runtime 调用时使用的 span link 目标。
    """

    runtime: Literal["claude_code", "codex"]
    operation: Literal["runtime.tool", "mcp.tools.call"]
    component: Literal["runtime", "mcp"]
    started_at: datetime
    context: TraceContext
    runtime_context: TraceContext | None


class AgentTelemetryObserver:
    """按 session/item ID 配对统一事件，并只提交受控调用事实。"""

    def __init__(self, port: TelemetryPort) -> None:
        """保存遥测端口和有界于当前活动会话/工具的配对状态。

        Args:
            port: 当前应用持有的非阻塞遥测提交端口。
        """

        self._port = port
        self._prepared: dict[str, list[_PreparedTurn]] = {}
        self._turns: dict[str, _ActiveTurn] = {}
        self._tools: dict[tuple[str, str], _ActiveTool] = {}
        self._lock = threading.Lock()

    def prepare_turn(
        self,
        session_id: str,
        runtime: Literal["claude_code", "codex"],
        *,
        prepared_at: datetime | None = None,
    ) -> str:
        """在调用 runtime 前保存当前 HTTP 上下文，供 turn_start 建立 link。

        Args:
            session_id: Trowel 会话 ID，只在写入前作为不可逆摘要来源。
            runtime: 会话使用 Claude Code 还是 Codex。
            prepared_at: 测试可注入的调用开始时刻。

        Returns:
            只在当前进程内使用的观察代次，用于阻止旧请求误收口新 turn。
        """

        prepared = _PreparedTurn(
            runtime=runtime,
            prepared_at=prepared_at or datetime.now(UTC),
            context=create_span_context(),
        )
        with self._lock:
            self._prepared.setdefault(session_id, []).append(prepared)
        return prepared.context.span_id

    def abort_turn(self, session_id: str, observation_id: str) -> None:
        """把未产生正常 terminal 的活动 turn 收口为 error。

        Args:
            session_id: runtime 调用异常结束的 Trowel 会话 ID。
            observation_id: prepare_turn 返回的观察代次。
        """

        with self._lock:
            pending = self._prepared.get(session_id, [])
            self._prepared[session_id] = [
                prepared
                for prepared in pending
                if prepared.context.span_id != observation_id
            ]
            if not self._prepared[session_id]:
                self._prepared.pop(session_id, None)
        self._finish_turn(
            session_id,
            "error",
            expected_observation_id=observation_id,
        )

    def __call__(self, payload: Mapping[str, Any]) -> None:
        """消费一条统一 AgentEvent，只读取类型和受控关联字段。

        Args:
            payload: SessionHub 已转换的 AgentEvent 映射；正文、参数和结果不会保存。
        """

        session_id = _string(payload.get("session_id"))
        runtime = _runtime(payload.get("runtime"))
        event_type = _string(payload.get("type"))
        if session_id is None or runtime is None or event_type is None:
            return
        if event_type == "turn_start":
            self._start_turn(session_id, runtime, payload)
        elif event_type == "tool_call":
            self._start_tool(session_id, runtime, payload)
        elif event_type == "tool_result":
            self._finish_tool(session_id, payload)
        elif event_type in _TERMINAL_TYPES:
            self._finish_turn(session_id, event_type)

    def _start_turn(
        self,
        session_id: str,
        runtime: Literal["claude_code", "codex"],
        payload: Mapping[str, Any],
    ) -> None:
        """把真实 turn_start 与最近一次显式 runtime 请求配对。"""

        with self._lock:
            pending = self._prepared.get(session_id, [])
            prepared_index = next(
                (
                    index
                    for index, candidate in enumerate(pending)
                    if candidate.runtime == runtime
                ),
                None,
            )
            prepared = (
                pending.pop(prepared_index) if prepared_index is not None else None
            )
            if not pending:
                self._prepared.pop(session_id, None)
            if prepared is None:
                prepared = _PreparedTurn(
                    runtime=runtime,
                    prepared_at=datetime.now(UTC),
                    context=create_span_context(inherit_current=False),
                )
            turn_id = _string(payload.get("turn_id"))
            self._turns[session_id] = _ActiveTurn(
                runtime=runtime,
                turn_id=turn_id,
                started_at=prepared.prepared_at,
                context=prepared.context,
                runtime_started_at=datetime.now(UTC),
                runtime_context=create_span_context(inherit_current=False),
            )

    def _start_tool(
        self,
        session_id: str,
        runtime: Literal["claude_code", "codex"],
        payload: Mapping[str, Any],
    ) -> None:
        """只保存工具 ID、种类和所属 turn，不保留 input。"""

        item_id = _event_item_id(payload)
        if item_id is None:
            return
        event_payload = _mapping(payload.get("payload"))
        tool_name = _string(event_payload.get("tool_name")) or ""
        tool_input = _mapping(event_payload.get("input"))
        is_mcp = tool_name.startswith("mcp__") or bool(
            _string(tool_input.get("server"))
        )
        with self._lock:
            turn = self._turns.get(session_id)
            self._tools[(session_id, item_id)] = _ActiveTool(
                runtime=runtime,
                operation="mcp.tools.call" if is_mcp else "runtime.tool",
                component="mcp" if is_mcp else "runtime",
                started_at=datetime.now(UTC),
                context=create_span_context(inherit_current=False),
                runtime_context=(turn.runtime_context if turn is not None else None),
            )

    def _finish_tool(self, session_id: str, payload: Mapping[str, Any]) -> None:
        """按工具 ID 结束调用，并使用 runtime 明确给出的耗时和终态。"""

        item_id = _event_item_id(payload)
        if item_id is None:
            return
        with self._lock:
            tool = self._tools.pop((session_id, item_id), None)
        if tool is None:
            return
        event_payload = _mapping(payload.get("payload"))
        ended_at = datetime.now(UTC)
        duration_ms = _duration_ms(event_payload.get("duration_ms"))
        started_at = (
            ended_at - timedelta(milliseconds=duration_ms)
            if duration_ms is not None
            else tool.started_at
        )
        status_value = (_string(event_payload.get("status")) or "").lower()
        exit_code = event_payload.get("exit_code")
        failed = status_value in _ERROR_STATUSES or (
            isinstance(exit_code, int)
            and not isinstance(exit_code, bool)
            and exit_code != 0
        )
        attributes: dict[str, object] = {
            "quality": "reliable",
            "transport": "stdio",
        }
        if failed:
            attributes["error_category"] = (
                "cancelled" if status_value == "cancelled" else "unknown"
            )
        emit_span(
            self._port,
            component=tool.component,
            operation=tool.operation,
            started_at=started_at,
            ended_at=ended_at,
            status="error" if failed else "ok",
            runtime=tool.runtime,
            session_ref=session_id,
            call_ref=item_id,
            attributes=attributes,
            span_context=tool.context,
            links=((trace_link(tool.runtime_context),) if tool.runtime_context else ()),
        )

    def _finish_turn(
        self,
        session_id: str,
        event_type: str,
        *,
        expected_observation_id: str | None = None,
    ) -> None:
        """提交 turn 外层区间，并把未结束工具降级为 unavailable。"""

        with self._lock:
            turn = self._turns.get(session_id)
            if expected_observation_id is not None and (
                turn is None or turn.context.span_id != expected_observation_id
            ):
                return
            turn = self._turns.pop(session_id, None)
            unfinished = [
                (key, value)
                for key, value in self._tools.items()
                if key[0] == session_id
                and (
                    turn is None
                    or value.runtime_context is None
                    or value.runtime_context.span_id == turn.runtime_context.span_id
                )
            ]
            for key, _value in unfinished:
                self._tools.pop(key, None)
        ended_at = datetime.now(UTC)
        for (_session, item_id), tool in unfinished:
            emit_span(
                self._port,
                component=tool.component,
                operation=tool.operation,
                started_at=tool.started_at,
                ended_at=ended_at,
                status="error",
                runtime=tool.runtime,
                session_ref=session_id,
                call_ref=item_id,
                attributes={
                    "quality": "partial",
                    "transport": "stdio",
                    "error_category": "unavailable",
                },
                span_context=tool.context,
                links=(
                    (trace_link(tool.runtime_context),) if tool.runtime_context else ()
                ),
            )
        if turn is None:
            return
        failed = event_type != "finished"
        emit_span(
            self._port,
            component="runtime",
            operation="runtime.call",
            started_at=turn.runtime_started_at,
            ended_at=ended_at,
            status="error" if failed else "ok",
            runtime=turn.runtime,
            session_ref=session_id,
            call_ref=turn.turn_id,
            attributes={
                "quality": "partial",
                "transport": "stdio",
                "black_box": True,
                **(
                    {"error_category": "cancelled"}
                    if event_type == "interrupted"
                    else {}
                ),
            },
            span_context=turn.runtime_context,
            links=(trace_link(turn.context),),
        )
        emit_span(
            self._port,
            component="agent_host",
            operation="agent.turn",
            started_at=turn.started_at,
            ended_at=ended_at,
            status="error" if failed else "ok",
            runtime=turn.runtime,
            session_ref=session_id,
            call_ref=turn.turn_id,
            attributes={
                "quality": "reliable",
                "transport": "stdio",
                **(
                    {"error_category": "cancelled"}
                    if event_type == "interrupted"
                    else {}
                ),
            },
            span_context=turn.context,
        )


def _event_item_id(payload: Mapping[str, Any]) -> str | None:
    """优先读取 AgentEvent.item_id，再回退到通用 payload.tool_use_id。"""

    return _string(payload.get("item_id")) or _string(
        _mapping(payload.get("payload")).get("tool_use_id")
    )


def _mapping(value: object) -> Mapping[str, Any]:
    """只接受已经转换的普通映射，其他值视为空映射。"""

    return value if isinstance(value, Mapping) else {}


def _string(value: object) -> str | None:
    """只接受非空字符串，不把任意协议对象转成遥测身份。"""

    return value if isinstance(value, str) and value else None


def _runtime(value: object) -> Literal["claude_code", "codex"] | None:
    """把统一事件 runtime 收窄到遥测允许的两个值。"""

    return value if value in {"claude_code", "codex"} else None


def _duration_ms(value: object) -> float | None:
    """只接受一天以内的有限非负 runtime 耗时。"""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    duration = float(value)
    return duration if 0 <= duration <= 86_400_000 else None
