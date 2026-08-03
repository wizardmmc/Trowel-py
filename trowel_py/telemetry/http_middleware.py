"""记录受控 FastAPI 路由组到响应头就绪的耗时，不保存动态 URL。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from trowel_py.telemetry.events import emit_span
from trowel_py.telemetry.port import NoopTelemetryPort


class RuntimeTelemetryMiddleware:
    """用纯 ASGI send 包装器观察响应开始，不改变流式响应的 reader 数量。"""

    def __init__(self, app: ASGIApp) -> None:
        """保存下游 ASGI 应用。

        Args:
            app: FastAPI/Starlette 生成的下游 ASGI 应用。
        """

        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """只观察 HTTP 响应起点，并让原请求/响应消息原样通过。

        Args:
            scope: 当前 ASGI 请求上下文。
            receive: 下游读取请求消息的函数。
            send: 下游发送响应消息的函数。
        """

        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        started_at = datetime.now(UTC)
        recorded = False

        async def observe_send(message: Message) -> None:
            """在首个响应头消息到达时记录路由组耗时。"""

            nonlocal recorded
            if message["type"] == "http.response.start" and not recorded:
                recorded = True
                self._record(
                    scope,
                    started_at,
                    datetime.now(UTC),
                    int(message["status"]),
                )
            await send(message)

        try:
            await self._app(scope, receive, observe_send)
        except BaseException:
            if not recorded:
                self._record(scope, started_at, datetime.now(UTC), 500)
            raise

    @staticmethod
    def _record(
        scope: Scope,
        started_at: datetime,
        ended_at: datetime,
        status_code: int,
    ) -> None:
        """把路由模板映射成固定 operation 后提交 span。

        Args:
            scope: 路由完成匹配后的 ASGI 请求上下文。
            started_at: 请求进入中间件的时刻。
            ended_at: 响应头就绪或异常抛出的时刻。
            status_code: HTTP 响应码；未处理异常按 500。
        """

        operation = _operation_for_scope(scope)
        if operation is None:
            return
        app: Any = scope.get("app")
        port = getattr(getattr(app, "state", None), "telemetry_port", None)
        emit_span(
            port or NoopTelemetryPort(),
            component="fastapi",
            operation=operation,
            started_at=started_at,
            ended_at=ended_at,
            status="error" if status_code >= 500 else "ok",
            attributes={"transport": "http", "quality": "reliable"},
        )


def _operation_for_scope(scope: Scope) -> str | None:
    """只根据 Starlette 路由模板返回低基数 operation。

    Args:
        scope: 已经过路由匹配的 ASGI 请求上下文。

    Returns:
        统计或 Agent SSE operation；其他路由为 None。
    """

    route = scope.get("route")
    template = getattr(route, "path", "")
    if template.startswith("/api/statistics/"):
        return "http.statistics.query"
    if template in {
        "/api/agent/sessions/{session_id}/events",
        "/api/agent/sessions/{session_id}/messages",
    }:
        return "http.agent.messages"
    return None
