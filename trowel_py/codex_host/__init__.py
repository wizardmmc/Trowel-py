"""导出 Codex app-server 主机的公开接口。

Trowel 后端通过一个 ``CodexHostManager`` 管理共享的 app-server 进程；该进程
在首次请求时启动，连接失效后可以重启。多个 Codex thread 共用当前进程，
``CodexHostManager`` 按 ``threadId`` 将通知交给对应的 session。
"""

from __future__ import annotations

from trowel_py.codex_host.errors import (
    CodexHostError,
    ProtocolViolationError,
    ServerRequestUnsupportedError,
    TransportClosedError,
    VersionMismatchError,
)
from trowel_py.codex_host.events import (
    CodexEvent,
    CodexEventType,
    HostStatusKind,
    TranslatedItem,
)
from trowel_py.codex_host.manager import (
    CodexHostManager,
    CodexHostManagerState,
    OrphanDiagnostic,
)
from trowel_py.codex_host.protocol import (
    SUPPORTED_CODEX_VERSION,
    ClientInfo,
    MessageKind,
    classify_server_message,
)
from trowel_py.codex_host.session import (
    CodexSession,
    CodexSessionConfig,
    CodexSessionState,
    ThreadBinding,
    TurnConflictError,
    parse_thread_binding,
)
from trowel_py.codex_host.translator import CodexTranslator
from trowel_py.codex_host.transport import AppServerClient
from trowel_py.codex_host.version import read_codex_version

__all__ = [
    "AppServerClient",
    "ClientInfo",
    "MessageKind",
    "SUPPORTED_CODEX_VERSION",
    "classify_server_message",
    "read_codex_version",
    "CodexEvent",
    "CodexEventType",
    "CodexTranslator",
    "HostStatusKind",
    "TranslatedItem",
    "CodexSession",
    "CodexSessionConfig",
    "CodexSessionState",
    "ThreadBinding",
    "TurnConflictError",
    "parse_thread_binding",
    "CodexHostManager",
    "CodexHostManagerState",
    "OrphanDiagnostic",
    "CodexHostError",
    "ProtocolViolationError",
    "ServerRequestUnsupportedError",
    "TransportClosedError",
    "VersionMismatchError",
]
