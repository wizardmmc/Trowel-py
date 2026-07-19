"""trowel_py.codex_host — Codex app-server host for trowel (M9).

slice-070 shipped the transport kernel (:class:`AppServerClient`).
slice-071 layers the shared :class:`CodexHostManager`, lightweight
:class:`CodexSession` thread bindings and the Codex event translator on top.

One backend process holds one manager, one manager holds one app-server
process, and many Codex threads share that one process — routing every
notification back to the owning session by ``threadId``.
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
    # transport (slice-070)
    "AppServerClient",
    "ClientInfo",
    "MessageKind",
    "SUPPORTED_CODEX_VERSION",
    "classify_server_message",
    "read_codex_version",
    # events + translator (slice-071)
    "CodexEvent",
    "CodexEventType",
    "CodexTranslator",
    "HostStatusKind",
    "TranslatedItem",
    # session (slice-071)
    "CodexSession",
    "CodexSessionConfig",
    "CodexSessionState",
    "ThreadBinding",
    "TurnConflictError",
    "parse_thread_binding",
    # manager (slice-071)
    "CodexHostManager",
    "CodexHostManagerState",
    "OrphanDiagnostic",
    # errors
    "CodexHostError",
    "ProtocolViolationError",
    "ServerRequestUnsupportedError",
    "TransportClosedError",
    "VersionMismatchError",
]
