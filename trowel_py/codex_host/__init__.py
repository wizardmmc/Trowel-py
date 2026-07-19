"""trowel_py.codex_host — Codex app-server stdio transport (slice-070).

Owns the single JSONL stdio connection to ``codex app-server --stdio
--disable memories``. One reader, one writer, request-id future correlation,
server-request fan-out, version lock and secret redaction.

This slice ships only the transport kernel. Thread/turn state machines
(slice-071), Session Hub (slice-072) and approval UI (slice-075) layer on top.
"""

from __future__ import annotations

from trowel_py.codex_host.errors import (
    CodexHostError,
    ProtocolViolationError,
    ServerRequestUnsupportedError,
    TransportClosedError,
    VersionMismatchError,
)
from trowel_py.codex_host.protocol import (
    SUPPORTED_CODEX_VERSION,
    ClientInfo,
    MessageKind,
    classify_server_message,
)
from trowel_py.codex_host.transport import AppServerClient
from trowel_py.codex_host.version import read_codex_version

__all__ = [
    "AppServerClient",
    "ClientInfo",
    "CodexHostError",
    "MessageKind",
    "ProtocolViolationError",
    "SUPPORTED_CODEX_VERSION",
    "ServerRequestUnsupportedError",
    "TransportClosedError",
    "VersionMismatchError",
    "classify_server_message",
    "read_codex_version",
]
