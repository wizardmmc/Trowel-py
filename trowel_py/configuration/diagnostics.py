"""把连接网络、runtime 启动和 Claude 兼容反代分层投影为诊断状态。"""

from __future__ import annotations

from typing import Any

from trowel_py.agent_host.binding import Runtime
from trowel_py.agent_host.runtime_availability import detect_runtime_availability
from trowel_py.configuration.models import ConnectionKind, ConnectionView, RuntimeKind


def build_diagnostics(connections: tuple[ConnectionView, ...]) -> dict[str, Any]:
    """返回不会把一层成功冒充整条连接可用的三层诊断。"""

    available = detect_runtime_availability()
    items: list[dict[str, Any]] = []
    for connection in connections:
        network = _network_status(connection)
        runtime = _runtime_status(connection, available)
        proxy = _proxy_status(connection)
        items.append(
            {
                "connection_id": connection.id,
                "connection_name": connection.name,
                "network": network,
                "runtime_launch": runtime,
                "trowel_proxy": proxy,
            }
        )
    return {"connections": items}


def _network_status(connection: ConnectionView) -> dict[str, str | None]:
    """从最近模型列表请求给出连接网络层状态。"""

    if connection.catalog.status == "ready":
        return {"status": "available", "code": None}
    if connection.catalog.status == "error":
        return {"status": "unavailable", "code": connection.catalog.error_code}
    return {"status": "unknown", "code": "NOT_CHECKED"}


def _runtime_status(
    connection: ConnectionView,
    available: dict[Runtime, bool],
) -> dict[str, str | None]:
    """只报告本机 runtime 是否存在，不外推连接协议能力。"""

    if connection.runtime == RuntimeKind.DIRECT_API:
        return {"status": "not_applicable", "code": None}
    runtime = (
        Runtime.CLAUDE_CODE
        if connection.runtime == RuntimeKind.CLAUDE_CODE
        else Runtime.CODEX
    )
    return (
        {"status": "available", "code": None}
        if available.get(runtime, False)
        else {"status": "unavailable", "code": "RUNTIME_NOT_INSTALLED"}
    )


def _proxy_status(connection: ConnectionView) -> dict[str, str | None]:
    """明确 Claude 多连接反向代理当前尚未实现。"""

    if connection.kind == ConnectionKind.CLAUDE_COMPATIBLE:
        return {
            "status": "unsupported",
            "code": "CONNECTION_PROXY_NOT_IMPLEMENTED",
        }
    return {"status": "not_applicable", "code": None}
