"""为业务边界构造不会反向影响主流程的受控遥测事实。"""

from __future__ import annotations

import logging
import re
import secrets
import uuid
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from trowel_py.telemetry.contracts import (
    TelemetryAttributes,
    TelemetryMetricInput,
    TelemetrySpanInput,
    TraceLinkInput,
)
from trowel_py.telemetry.port import TelemetryPort

logger = logging.getLogger(__name__)

_TRACEPARENT_PATTERN = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")
_ZERO_TRACE_ID = "0" * 32
_ZERO_SPAN_ID = "0" * 16


@dataclass(frozen=True)
class TraceContext:
    """记录一个 span 在当前 trace 中的稳定位置。

    Attributes:
        trace_id: 当前调用链共用的 16 字节十六进制身份。
        span_id: 当前操作自己的 8 字节十六进制身份。
        parent_span_id: 真实传播父上下文中的 span 身份；根 span 为 None。
        sampled: 上游是否请求采样；Trowel 仍按自己的有界策略决定是否记录。
    """

    trace_id: str
    span_id: str
    parent_span_id: str | None
    sampled: bool = True


_CURRENT_TRACE_CONTEXT: ContextVar[TraceContext | None] = ContextVar(
    "trowel_current_trace_context",
    default=None,
)


def parse_traceparent(value: str | None) -> TraceContext | None:
    """解析 W3C ``traceparent`` v00；无效或不支持的值不参与父子关联。

    Args:
        value: HTTP 请求携带的完整 ``traceparent`` 字段。

    Returns:
        可作为远端父 span 的上下文；格式无效时为 None。
    """

    if value is None:
        return None
    match = _TRACEPARENT_PATTERN.fullmatch(value.strip())
    if match is None:
        return None
    trace_id, span_id, flags = match.groups()
    if trace_id == _ZERO_TRACE_ID or span_id == _ZERO_SPAN_ID:
        return None
    return TraceContext(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=None,
        sampled=bool(int(flags, 16) & 0x01),
    )


def format_traceparent(context: TraceContext) -> str:
    """把当前 span 编码成传给下一层的 W3C ``traceparent`` v00。

    Args:
        context: 下一层应当认作父 span 的上下文。

    Returns:
        只包含版本、trace、父 span 和采样位的标准字段值。
    """

    flags = "01" if context.sampled else "00"
    return f"00-{context.trace_id}-{context.span_id}-{flags}"


def current_trace_context() -> TraceContext | None:
    """返回当前异步调用链显式激活的 span 上下文。"""

    return _CURRENT_TRACE_CONTEXT.get()


def create_span_context(
    *,
    parent: TraceContext | None = None,
    inherit_current: bool = True,
) -> TraceContext:
    """创建一个新 span，并只继承调用方明确提供或当前激活的父上下文。

    Args:
        parent: 已经通过真实 carrier 或进程内调用栈传播的父 span；省略时读取
            当前 ``ContextVar``。
        inherit_current: parent 为 None 时是否继承当前调用栈；False 明确创建新
            trace，供只能使用 span link 的 runtime 黑盒边界使用。

    Returns:
        已分配 trace/span identity、尚未提交耗时事实的新上下文。
    """

    actual_parent = (
        parent
        if parent is not None
        else current_trace_context()
        if inherit_current
        else None
    )
    if actual_parent is None:
        return TraceContext(
            trace_id=secrets.token_hex(16),
            span_id=secrets.token_hex(8),
            parent_span_id=None,
        )
    return TraceContext(
        trace_id=actual_parent.trace_id,
        span_id=secrets.token_hex(8),
        parent_span_id=actual_parent.span_id,
        sampled=actual_parent.sampled,
    )


@contextmanager
def activate_trace_context(context: TraceContext) -> Iterator[TraceContext]:
    """在当前同步或异步调用栈中激活一个 span，退出时恢复原上下文。

    Args:
        context: 下游进程内操作可以认作父 span 的上下文。

    Yields:
        与传入值相同的上下文，供调用方在退出前完成当前 span。
    """

    token = _CURRENT_TRACE_CONTEXT.set(context)
    try:
        yield context
    finally:
        _CURRENT_TRACE_CONTEXT.reset(token)


def trace_link(context: TraceContext) -> TraceLinkInput:
    """把不能冒充父子的已知上下文转换成 span link。

    Args:
        context: 与新 span 存在可核查关联的上下文。

    Returns:
        只包含随机 trace/span identity 的安全 link。
    """

    return TraceLinkInput(trace_id=context.trace_id, span_id=context.span_id)


def emit_span(
    port: TelemetryPort,
    *,
    component: str,
    operation: str,
    started_at: datetime,
    ended_at: datetime,
    status: str = "ok",
    runtime: str | None = None,
    model: str | None = None,
    session_ref: str | None = None,
    call_ref: str | None = None,
    attributes: dict[str, Any] | None = None,
    span_context: TraceContext | None = None,
    links: Sequence[TraceLinkInput | TraceContext | Mapping[str, object]] = (),
) -> None:
    """构造并非阻塞提交一条 span，任何观测错误都只写日志。

    Args:
        port: 当前应用持有的遥测提交端口。
        component: 集中目录中的受控组件。
        operation: 不含动态身份的受控操作名。
        started_at: 操作开始的带时区墙钟时刻。
        ended_at: 操作结束的带时区墙钟时刻。
        status: ok、error 或 unset。
        runtime: 可选的 Claude Code 或 Codex 分类。
        model: runtime 明确报告时使用的模型名。
        session_ref: 可选本机会话引用，入库前会做不可逆摘要。
        call_ref: 可选原生轮次或工具调用引用，入库前会做不可逆摘要。
        attributes: 仅含 schema 白名单低基数字段的映射。
        span_context: 当前操作预先分配的上下文；省略时创建当前 span 的子 span。
        links: 已核查但不能表示为父子的 trace/span 关联。
    """

    try:
        context = span_context or create_span_context()
        port.emit_span(
            TelemetrySpanInput(
                trace_id=context.trace_id,
                span_id=context.span_id,
                parent_span_id=context.parent_span_id,
                started_at=started_at,
                ended_at=ended_at,
                component=component,
                operation=operation,
                status=status,
                runtime=runtime,
                model=model,
                session_ref=session_ref,
                call_ref=call_ref,
                attributes=TelemetryAttributes.model_validate(attributes or {}),
                links=[_coerce_trace_link(link) for link in links],
            )
        )
    except Exception:
        logger.debug("[telemetry] span construction failed", exc_info=True)


def _coerce_trace_link(
    value: TraceLinkInput | TraceContext | Mapping[str, object],
) -> TraceLinkInput:
    """把内部上下文或 wire 映射统一转换成严格的 link 模型。

    Args:
        value: 已校验 link、当前上下文或含 trace/span ID 的映射。

    Returns:
        可直接写入 ``TelemetrySpanInput`` 的 link。
    """

    if isinstance(value, TraceLinkInput):
        return value
    if isinstance(value, TraceContext):
        return trace_link(value)
    return TraceLinkInput.model_validate(value)


def emit_metric(
    port: TelemetryPort,
    *,
    component: str,
    name: str,
    kind: str,
    unit: str,
    value: float,
    status: str = "ok",
    operation: str | None = None,
    observed_at: datetime | None = None,
    attributes: dict[str, Any] | None = None,
) -> None:
    """构造并非阻塞提交一条数值样本，任何观测错误都只写日志。

    Args:
        port: 当前应用持有的遥测提交端口。
        component: 集中目录中的受控组件。
        name: 集中目录中的指标名。
        kind: counter、gauge 或 histogram。
        unit: 1、By 或 ms。
        value: 有限非负样本值。
        status: ok、error 或 unset。
        operation: 可选受控操作维度。
        observed_at: 采样时刻；省略时使用当前 UTC 时间。
        attributes: 仅含 schema 白名单低基数字段的映射。
    """

    try:
        port.emit_metric(
            TelemetryMetricInput(
                metric_id=f"metric-{uuid.uuid4().hex}",
                observed_at=observed_at or datetime.now(UTC),
                component=component,
                name=name,
                kind=kind,
                unit=unit,
                value=value,
                status=status,
                runtime=None,
                model=None,
                operation=operation,
                attributes=TelemetryAttributes.model_validate(attributes or {}),
            )
        )
    except Exception:
        logger.debug("[telemetry] metric construction failed", exc_info=True)
