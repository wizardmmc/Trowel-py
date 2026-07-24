"""Codex app-server transport 的异常层级。

该错误族覆盖 app-server 明确返回的失败、协议偏离、版本不兼容和安全拒绝；发送
异常、timeout 与任务取消仍保留 Python 原生异常语义。
"""

from __future__ import annotations

from typing import Any


class CodexHostError(Exception):
    pass


class TransportClosedError(CodexHostError):
    """app-server 已退出或 transport 已关闭。

    关闭后调用、reader 读到 EOF 或进程异常退出都会触发此错误；所有 pending
    request 也会一并失败，避免 Future 永久等待。
    """

    def __init__(self, message: str, *, exit_code: int | None = None) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class ProtocolViolationError(CodexHostError):
    """app-server 明确返回错误，或其 payload 偏离已验证结构。

    translator、catalog 与 session 使用此异常拒绝无法安全映射的数据，但异常本身
    不会关闭 transport。
    """

    def __init__(self, message: str, *, payload: Any = None) -> None:
        """保留原始 payload 供诊断；写日志前必须由调用者脱敏。"""
        super().__init__(message)
        self.payload = payload


class VersionMismatchError(CodexHostError):
    """已安装 Codex CLI 不在验证过的版本范围内。"""

    def __init__(self, installed: str, supported: str) -> None:
        self.installed = installed
        self.supported = supported
        super().__init__(
            f"Codex protocol version '{installed}' is not in the validated "
            f"window (supported: {supported})"
        )


class ServerRequestUnsupportedError(CodexHostError):
    """handler 安全拒绝 server request 的内部信号。

    transport 捕获此异常并回复 JSON-RPC method-not-found；它不会逃到外层调用者，
    也绝不将未知请求自动批准。
    """

    def __init__(self, method: str, request_id: object) -> None:
        self.method = method
        self.request_id = request_id
        super().__init__(
            f"No handler registered for server request {method!r} (id={request_id!r}); "
            "replying with a structured error instead of auto-approving."
        )
