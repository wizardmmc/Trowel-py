"""Exception hierarchy for the Codex app-server transport.

Every failure that closes the transport or fails a pending request raises a
subclass of :class:`CodexHostError`, so callers can catch the whole family
without swallowing unrelated bugs. The split mirrors the failure modes the
slice-070 spec calls out: process exit, bad messages, version drift and
unsupported server-initiated requests.
"""

from __future__ import annotations

from typing import Any


class CodexHostError(Exception):
    """Base class for every Codex app-server transport failure."""


class TransportClosedError(CodexHostError):
    """The app-server process is gone or the transport was closed.

    Raised both when a caller invokes a method after close and when the
    reader task observes EOF / a non-zero exit. Every pending request fails
    with this exception so no future is left waiting forever (spec C-5).
    """

    def __init__(self, message: str, *, exit_code: int | None = None) -> None:
        """Stamp the optional process exit code for diagnostics.

        Args:
            message: Human-readable description of why the transport closed.
            exit_code: The app-server exit code when observable, else None.
        """

        super().__init__(message)
        self.exit_code = exit_code


class ProtocolViolationError(CodexHostError):
    """A server message did not match the JSON-RPC shapes the protocol allows.

    Covers: unparseable JSON, a non-object payload, a response whose id is not
a pending request, or a duplicate response id. Per spec §2 these are recorded
    as diagnostics; the ones that signal real protocol inconsistency escalate
    the transport to failed.
    """

    def __init__(self, message: str, *, payload: Any = None) -> None:
        """Keep the offending payload for redacted diagnostic logging.

        Args:
            message: What was wrong with the message.
            payload: The raw parsed object (already redacted before logging).
        """

        super().__init__(message)
        self.payload = payload


class VersionMismatchError(CodexHostError):
    """The installed Codex CLI is outside the validated version window.

    Attributes:
        installed: The version string read from ``codex --version``.
        supported: The version this transport was validated against.
    """

    def __init__(self, installed: str, supported: str) -> None:
        """Store both versions so callers can surface them in the UI.

        Args:
            installed: The version reported by the installed ``codex`` binary.
            supported: The pinned baseline version (see ``protocol.SUPPORTED``).
        """

        self.installed = installed
        self.supported = supported
        super().__init__(
            f"Codex protocol version '{installed}' is not in the validated "
            f"window (supported: {supported})"
        )


class ServerRequestUnsupportedError(CodexHostError):
    """A server-initiated request had no registered handler.

    Per spec C-3 the transport never auto-approves an unknown bidirectional
    request. It replies with a structured JSON-RPC error and raises this
    locally so the caller can decide whether to log or surface it.

    Attributes:
        method: The server request method name.
        request_id: The server request id (echoed in the error response).
    """

    def __init__(self, method: str, request_id: object) -> None:
        """Record the method and id for correlation.

        Args:
            method: The unsupported ``method`` from the server request.
            request_id: The ``id`` the server assigned to the request.
        """

        self.method = method
        self.request_id = request_id
        super().__init__(
            f"No handler registered for server request {method!r} (id={request_id!r}); "
            "replying with a structured error instead of auto-approving."
        )
