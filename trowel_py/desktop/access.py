"""用当前桌面实例的随机凭据限制对 sidecar API 的访问。"""

from __future__ import annotations

import hmac
from urllib.parse import urlsplit

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class DesktopCredentialMiddleware:
    """仅在 Host 提供凭据时保护本地 ``/api`` 路径。

    Attributes:
        app: 凭据通过后继续处理请求的下游 ASGI 应用。
        credential: 当前桌面实例生成的随机凭据；为空表示 browser 模式。
    """

    def __init__(self, app: ASGIApp, credential: str | None = None) -> None:
        """保存下游应用和当前桌面实例凭据。

        Args:
            app: 凭据通过后继续处理请求的下游 ASGI 应用。
            credential: Electron Host 通过环境变量传入的实例凭据；未传时不改变
                browser 模式的既有访问方式。
        """
        self.app = app
        self.credential = credential.strip() if credential else ""

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """校验桌面 API 的 Bearer 凭据，并把其他请求交给下游应用。

        Args:
            scope: ASGI 请求范围；这里只读取请求类型、路径、方法和请求头。
            receive: 下游应用读取请求体使用的 ASGI 回调。
            send: 下游应用或拒绝响应写回数据使用的 ASGI 回调。
        """
        if not self._requires_credential(scope):
            await self.app(scope, receive, send)
            return

        supplied = _bearer_credential(scope)
        if supplied is not None and hmac.compare_digest(supplied, self.credential):
            await self.app(scope, receive, send)
            return

        response = JSONResponse(
            status_code=401,
            content={
                "success": False,
                "data": None,
                "error": "desktop credential required",
            },
        )
        await response(scope, receive, send)

    def _requires_credential(self, scope: Scope) -> bool:
        """判断当前 ASGI 请求是否属于需要实例凭据的桌面 API。"""
        return bool(
            self.credential
            and scope.get("type") == "http"
            and scope.get("method") != "OPTIONS"
            and str(scope.get("path", "")).startswith("/api/")
        )


def _bearer_credential(scope: Scope) -> str | None:
    """从 ASGI 请求头读取大小写不敏感的 Bearer 凭据。"""
    for raw_name, raw_value in scope.get("headers", []):
        if raw_name.lower() != b"authorization":
            continue
        value = raw_value.decode("latin-1")
        prefix = "Bearer "
        if value.startswith(prefix) and value[len(prefix) :]:
            return value[len(prefix) :]
    return None


def validate_desktop_renderer_origin(origin: str) -> str:
    """只接受 Electron 文件页或带显式端口的 loopback HTTP 来源。

    Args:
        origin: Host 根据本次 renderer 地址生成的 CORS origin。

    Returns:
        可直接交给 CORS 中间件的规范化来源。

    Raises:
        ValueError: 来源包含路径、凭据，或不是受控的本地 renderer。
    """
    if origin == "null":
        return origin
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("desktop renderer origin must be loopback HTTP or null")
    return f"http://{parsed.hostname}:{parsed.port}"
