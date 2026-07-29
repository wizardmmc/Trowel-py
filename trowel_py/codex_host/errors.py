"""定义 Codex Host 的领域异常及其诊断字段。

该错误族覆盖 app-server 明确返回的失败、协议偏离、版本不兼容和安全拒绝；底层
写入失败、超时和任务取消仍保留 Python 原生异常语义。
"""

from __future__ import annotations

from typing import Any


class CodexHostError(Exception):
    """Codex Host 传输、协议和安全拒绝错误的基类。"""

    pass


class TransportClosedError(CodexHostError):
    """app-server 已退出或 transport 已关闭。

    关闭后调用、读取到 EOF 或进程异常退出都会触发此错误；所有等待响应的请求也会
    一并失败，避免 Future 永久等待。

    Attributes:
        exit_code: app-server 进程退出码；尚未取得退出码时为 None。
    """

    def __init__(self, message: str, *, exit_code: int | None = None) -> None:
        """记录 transport 关闭原因和进程退出码。

        Args:
            message: 面向调用方的关闭原因。
            exit_code: app-server 进程退出码；尚未取得退出码时为 None。
        """

        super().__init__(message)
        self.exit_code = exit_code


class ProtocolViolationError(CodexHostError):
    """app-server 明确返回错误，或其 payload 偏离已验证结构。

    translator、catalog 与 session 使用此异常拒绝无法安全映射的数据，但异常本身
    不会关闭 transport。

    Attributes:
        payload: 触发错误的原始协议数据；没有可保留的数据时为 None。
    """

    def __init__(self, message: str, *, payload: Any = None) -> None:
        """记录协议错误和用于诊断的原始数据。

        Args:
            message: 面向调用方的协议错误说明。
            payload: 触发错误的原始协议数据；写入日志前必须由调用方脱敏。
        """
        super().__init__(message)
        self.payload = payload


class VersionMismatchError(CodexHostError):
    """表示已安装 Codex CLI 版本与 Trowel 验证的协议基线不一致。

    Attributes:
        installed: 从 ``codex --version`` 读取的当前 CLI 版本。
        supported: Trowel 用作协议基线的 Codex CLI 版本或版本范围。
    """

    def __init__(self, installed: str, supported: str) -> None:
        """保存版本字段，并构造可直接展示的兼容性错误消息。"""

        self.installed = installed
        self.supported = supported
        super().__init__(
            f"Codex protocol version '{installed}' is not in the validated "
            f"window (supported: {supported})"
        )


class ServerRequestUnsupportedError(CodexHostError):
    """表示服务端请求没有可安全处理的 handler。

    transport 捕获此异常并回复 JSON-RPC method-not-found；它不会逃到外层调用者，
    也绝不将未知请求自动批准。

    Attributes:
        method: 无法处理的 JSON-RPC 方法名。
        request_id: app-server 分配的请求 ID。
    """

    def __init__(self, method: str, request_id: object) -> None:
        """保存请求上下文，并构造明确拒绝自动批准的错误消息。"""

        self.method = method
        self.request_id = request_id
        super().__init__(
            f"No handler registered for server request {method!r} (id={request_id!r}); "
            "replying with a structured error instead of auto-approving."
        )
