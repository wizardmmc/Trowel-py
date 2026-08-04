"""把资源 owner 终态转换成低基数运行遥测。"""

from __future__ import annotations

from collections.abc import Callable

from trowel_py.resource_lifecycle.models import OwnerCloseObservation, OwnerScope
from trowel_py.telemetry.events import emit_metric, emit_span
from trowel_py.telemetry.port import TelemetryPort

_OWNER_OPERATIONS = {
    OwnerScope.APP: "resource.app.close",
    OwnerScope.RUNTIME_CONNECTION: "resource.runtime_connection.close",
    OwnerScope.SESSION: "resource.session.close",
    OwnerScope.TURN: "resource.turn.close",
}


def create_owner_close_observer(
    port: TelemetryPort,
) -> Callable[[OwnerCloseObservation], None]:
    """创建不持有 registry 的 owner 关闭观测回调。

    Args:
        port: 当前应用持有的非阻塞遥测端口。

    Returns:
        只接收去身份 owner 事实的同步回调。
    """

    def observe(observation: OwnerCloseObservation) -> None:
        """记录一次 owner 关闭终态 span 和残留资源 gauge。"""

        operation = _OWNER_OPERATIONS[observation.owner_scope]
        succeeded = observation.status == "closed"
        span_attributes: dict[str, object] = {
            "quality": "reliable",
            "row_count_bucket": _count_bucket(
                observation.closed_resource_count
            ),
        }
        if not succeeded:
            span_attributes["error_category"] = "unavailable"
        emit_span(
            port,
            component="runtime",
            operation=operation,
            started_at=observation.started_at,
            ended_at=observation.completed_at,
            status="ok" if succeeded else "error",
            attributes=span_attributes,
        )
        emit_metric(
            port,
            component="runtime",
            name="resource.remaining",
            kind="gauge",
            unit="1",
            value=observation.remaining_resource_count,
            status="ok" if succeeded else "error",
            operation=operation,
            observed_at=observation.completed_at,
            attributes={"quality": "reliable"},
        )

    return observe


def _count_bucket(value: int) -> str:
    """把资源数量压缩成 schema 允许的低基数区间。

    Args:
        value: 本次关闭提交的资源数量。

    Returns:
        遥测 attributes 允许的固定区间标签。
    """

    if value == 0:
        return "0"
    if value <= 10:
        return "1-10"
    if value <= 100:
        return "11-100"
    if value <= 1_000:
        return "101-1000"
    return "1000+"
