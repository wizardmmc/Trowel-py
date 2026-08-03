"""为业务边界构造不会反向影响主流程的受控遥测事实。"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from trowel_py.telemetry.contracts import (
    TelemetryAttributes,
    TelemetryMetricInput,
    TelemetrySpanInput,
)
from trowel_py.telemetry.port import TelemetryPort

logger = logging.getLogger(__name__)


def emit_span(
    port: TelemetryPort,
    *,
    component: str,
    operation: str,
    started_at: datetime,
    ended_at: datetime,
    status: str = "ok",
    session_ref: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> None:
    """构造并非阻塞提交一条 span，任何观测错误都只写日志。

    Args:
        port: 当前应用持有的遥测提交端口。
        component: 集中目录中的受控组件。
        operation: 不含动态身份的受控操作名。
        started_at: 操作开始的带时区墙钟时刻。
        ended_at: 操作结束的带时区墙钟时刻。
        status: ok、error 或 unset。
        session_ref: 可选本机会话引用，入库前会做不可逆摘要。
        attributes: 仅含 schema 白名单低基数字段的映射。
    """

    try:
        port.emit_span(
            TelemetrySpanInput(
                trace_id=secrets.token_hex(16),
                span_id=secrets.token_hex(8),
                parent_span_id=None,
                started_at=started_at,
                ended_at=ended_at,
                component=component,
                operation=operation,
                status=status,
                runtime=None,
                model=None,
                session_ref=session_ref,
                call_ref=None,
                attributes=TelemetryAttributes.model_validate(attributes or {}),
                links=[],
            )
        )
    except Exception:
        logger.debug("[telemetry] span construction failed", exc_info=True)


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
