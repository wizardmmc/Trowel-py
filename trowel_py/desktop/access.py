"""用当前桌面实例的随机凭据限制对 sidecar API 的访问。"""

from __future__ import annotations

import hmac
import hashlib
from urllib.parse import urlsplit
from urllib.parse import parse_qs

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class DesktopCredentialMiddleware:
    """仅在 Host 提供凭据时保护 renderer 使用的本地 ``/api`` 路径。

    Claude 子进程访问 ``POST /api/cc-runtime/<lease>/v1/...`` 时使用会话级
    随机租约令牌，原生 runtime 无法附带 Electron Host 的 Bearer，因此只有这两条
    内部反代路由租约自身鉴权。

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
        if _has_scoped_discussion_read_access(scope, self.credential):
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
        path = str(scope.get("path", ""))
        return bool(
            self.credential
            and scope.get("type") == "http"
            and scope.get("method") != "OPTIONS"
            and path.startswith("/api/")
            and not _is_claude_runtime_lease_request(scope)
        )


def _is_claude_runtime_lease_request(scope: Scope) -> bool:
    """只识别 Claude 原生 runtime 实际使用的 POST 租约反代路由。

    Args:
        scope: 包含 HTTP 方法和解码后路径的 ASGI 请求范围。

    Returns:
        路径形如 ``/api/cc-runtime/<lease>/v1/<rest>`` 且方法为 POST 时返回
        True；空租约、空代理路径或其他方法都返回 False。
    """

    if scope.get("type") != "http" or scope.get("method") != "POST":
        return False
    parts = str(scope.get("path", "")).split("/")
    return bool(
        len(parts) >= 6
        and parts[1:3] == ["api", "cc-runtime"]
        and parts[3]
        and parts[4] == "v1"
        and any(parts[5:])
    )


def _bearer_credential(scope: Scope) -> str | None:
    """从 ASGI 请求头读取大小写不敏感的 Bearer 凭据。"""
    for raw_name, raw_value in scope.get("headers", []):
        if not isinstance(raw_name, bytes) or not isinstance(raw_value, bytes):
            continue
        if raw_name.lower() != b"authorization":
            continue
        value = raw_value.decode("latin-1")
        prefix = "Bearer "
        if value.startswith(prefix) and value[len(prefix) :]:
            return value[len(prefix) :]
    return None


def build_scoped_discussion_read_token(credential: str, path: str) -> str:
    """生成只允许读取一个 transcript 路径的实例级能力令牌。

    Args:
        credential: Electron Host 为当前 sidecar 生成的随机凭据。
        path: 形如 ``/api/discussions/<id>/transcript`` 的绝对 API 路径。

    Returns:
        不暴露 renderer Bearer 的十六进制 HMAC。
    """

    return hmac.new(
        credential.encode("utf-8"),
        f"GET:{path}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _has_scoped_discussion_read_access(scope: Scope, credential: str) -> bool:
    """只接受绑定到一个 transcript GET 路径的查询令牌。"""

    if scope.get("type") != "http" or scope.get("method") != "GET":
        return False
    path = str(scope.get("path", ""))
    parts = path.split("/")
    if not (
        len(parts) == 5
        and parts[1:3] == ["api", "discussions"]
        and parts[3]
        and parts[4] == "transcript"
    ):
        return False
    raw_query = scope.get("query_string", b"")
    query = parse_qs(raw_query.decode("latin-1"), keep_blank_values=True)
    supplied = query.get("access_token", [None])
    if len(supplied) != 1 or supplied[0] is None:
        return False
    expected = build_scoped_discussion_read_token(credential, path)
    return hmac.compare_digest(supplied[0], expected)


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
