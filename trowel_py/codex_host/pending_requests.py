"""Connection-scoped pending server requests for Codex app-server.

The app-server assigns request ids, but those ids are only unique within one
stdio connection.  This registry adds the manager's connection generation,
binds every request to its trowel session, and owns the one-shot future that
the transport handler awaits until the user answers.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping


class PendingRequestKind(str, Enum):
    """The server-request kinds verified for the current Codex baseline."""

    COMMAND_APPROVAL = "command_approval"
    FILE_APPROVAL = "file_approval"
    UNKNOWN = "unknown"


class PendingRequestStatus(str, Enum):
    """Lifecycle states exposed to the Agent timeline."""

    PENDING = "pending"
    ANSWERED = "answered"
    EXPIRED = "expired"
    HOST_CLOSED = "host_closed"


class PendingRequestError(Exception):
    """Base class for request lookup and validation failures."""


class PendingRequestNotFoundError(PendingRequestError):
    """The public request id is unknown to this manager."""


class PendingRequestOwnershipError(PendingRequestError):
    """A session tried to answer a request owned by another session."""


class PendingRequestConflictError(PendingRequestError):
    """A request that is no longer pending received another answer."""


class PendingRequestDecisionError(PendingRequestError):
    """The requested decision was not advertised by app-server."""


@dataclass
class PendingRequest:
    """One server request and the future used to return its native response."""

    request_id: str
    native_request_id: Any
    generation: int
    session_id: str
    thread_id: str
    turn_id: str | None
    item_id: str | None
    kind: PendingRequestKind
    available_decisions: tuple[Any, ...]
    command: str | None
    cwd: str | None
    reason: str | None
    response: asyncio.Future[dict[str, Any]]
    status: PendingRequestStatus = PendingRequestStatus.PENDING
    decision: str | None = None
    auto_resolved: bool = False
    resolution_reason: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """Return the JSON-safe AgentEvent payload for this lifecycle state."""

        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
            "item_id": self.item_id,
            "approval_kind": self.kind.value,
            "command": self.command,
            "cwd": self.cwd,
            "reason": self.reason,
            "available_decisions": deepcopy(list(self.available_decisions)),
            "status": self.status.value,
            "decision": self.decision,
            "auto_resolved": self.auto_resolved,
            "resolution_reason": self.resolution_reason,
        }


class PendingRequestRegistry:
    """Own connection-scoped request identity and one-shot state transitions."""

    def __init__(self) -> None:
        """Create an empty registry."""

        self._requests: dict[str, PendingRequest] = {}

    def create(
        self,
        *,
        native_request_id: Any,
        generation: int,
        session_id: str,
        kind: PendingRequestKind,
        params: Mapping[str, Any],
    ) -> PendingRequest:
        """Register a request under its connection generation and owner.

        Args:
            native_request_id: The raw JSON-RPC id assigned by app-server.
            generation: The manager connection generation that received it.
            session_id: The owning trowel session id.
            kind: The verified request kind.
            params: Raw request params from the real protocol.

        Returns:
            The newly pending request.

        Raises:
            PendingRequestConflictError: If the generated public id exists.
            ValueError: If the request has no usable thread id.
        """

        thread_id = params.get("threadId")
        if not isinstance(thread_id, str) or not thread_id:
            raise ValueError("server request has no threadId")
        request_id = f"{generation}-{native_request_id}"
        if request_id in self._requests:
            raise PendingRequestConflictError(
                f"pending request {request_id!r} already exists"
            )
        raw_decisions = params.get("availableDecisions")
        available = (
            tuple(deepcopy(raw_decisions)) if isinstance(raw_decisions, list) else ()
        )
        pending = PendingRequest(
            request_id=request_id,
            native_request_id=native_request_id,
            generation=generation,
            session_id=session_id,
            thread_id=thread_id,
            turn_id=_optional_string(params.get("turnId")),
            item_id=_optional_string(params.get("itemId")),
            kind=kind,
            available_decisions=available,
            command=_optional_string(params.get("command")),
            cwd=_optional_string(params.get("cwd")),
            reason=_optional_string(params.get("reason")),
            response=asyncio.get_running_loop().create_future(),
        )
        self._requests[request_id] = pending
        return pending

    def get(self, request_id: str) -> PendingRequest | None:
        """Return one request without changing its state."""

        return self._requests.get(request_id)

    def list_for_session(self, session_id: str) -> tuple[PendingRequest, ...]:
        """Return all retained requests for a session in insertion order."""

        return tuple(
            request
            for request in self._requests.values()
            if request.session_id == session_id
        )

    def resolve(
        self, session_id: str, request_id: str, decision: str
    ) -> PendingRequest:
        """Validate ownership/choice and resolve a pending request once.

        Args:
            session_id: The answering trowel session.
            request_id: The public generation-scoped request id.
            decision: The advertised decision key selected by the UI.

        Returns:
            The answered request.

        Raises:
            PendingRequestNotFoundError: If the id is unknown.
            PendingRequestOwnershipError: If another session owns it.
            PendingRequestConflictError: If it is already terminal.
            PendingRequestDecisionError: If app-server did not advertise it.
        """

        request = self._require(request_id)
        if request.session_id != session_id:
            raise PendingRequestOwnershipError(
                f"request {request_id!r} belongs to another session"
            )
        if request.status is not PendingRequestStatus.PENDING:
            raise PendingRequestConflictError(
                f"request {request_id!r} is already {request.status.value}"
            )
        native_decision = _find_native_decision(
            request.available_decisions, decision
        )
        if native_decision is None:
            raise PendingRequestDecisionError(
                f"decision {decision!r} was not advertised for request {request_id!r}"
            )
        request.status = PendingRequestStatus.ANSWERED
        request.decision = decision
        request.response.set_result({"decision": deepcopy(native_decision)})
        return request

    def resolve_automatically(
        self, request_id: str, decision: str, *, reason: str
    ) -> PendingRequest:
        """Resolve a request with a protocol-safe decision not offered to UI.

        This path is reserved for fail-closed lifecycle behavior: file requests
        whose real payload cannot explain the change, and timeout cleanup.
        It deliberately bypasses advertised-choice validation but never accepts.
        """

        request = self._require_pending(request_id)
        request.status = PendingRequestStatus.ANSWERED
        request.decision = decision
        request.auto_resolved = True
        request.resolution_reason = reason
        request.response.set_result({"decision": decision})
        return request

    def expire(self, request_id: str) -> PendingRequest:
        """Expire one pending request and safely decline it on the wire."""

        request = self._require_pending(request_id)
        request.status = PendingRequestStatus.EXPIRED
        request.decision = "decline"
        request.auto_resolved = True
        request.resolution_reason = "request timed out"
        request.response.set_result({"decision": "decline"})
        return request

    def close_generation(self, generation: int) -> tuple[PendingRequest, ...]:
        """Mark every pending request from one dead connection host_closed."""

        return self._close_matching(
            lambda request: request.generation == generation,
            reason="app-server connection closed",
        )

    def close_session(self, session_id: str) -> tuple[PendingRequest, ...]:
        """Invalidate pending requests when their owning session is deleted."""

        return self._close_matching(
            lambda request: request.session_id == session_id,
            reason="session closed",
        )

    def resolve_turn_with_cancel(
        self, session_id: str, turn_id: str
    ) -> tuple[PendingRequest, ...]:
        """Resolve advertised cancel decisions before interrupting a turn."""

        resolved: list[PendingRequest] = []
        for request in self._requests.values():
            if (
                request.session_id == session_id
                and request.turn_id == turn_id
                and request.status is PendingRequestStatus.PENDING
                and _find_native_decision(request.available_decisions, "cancel")
                is not None
            ):
                resolved.append(self.resolve(session_id, request.request_id, "cancel"))
        return tuple(resolved)

    def _close_matching(
        self, predicate: Callable[[PendingRequest], bool], *, reason: str
    ) -> tuple[PendingRequest, ...]:
        """Apply host_closed to pending requests matching ``predicate``."""

        closed: list[PendingRequest] = []
        for request in self._requests.values():
            if predicate(request) and request.status is PendingRequestStatus.PENDING:
                request.status = PendingRequestStatus.HOST_CLOSED
                request.resolution_reason = reason
                request.response.cancel()
                closed.append(request)
        return tuple(closed)

    def _require(self, request_id: str) -> PendingRequest:
        """Return an existing request or raise the public not-found error."""

        request = self._requests.get(request_id)
        if request is None:
            raise PendingRequestNotFoundError(
                f"pending request {request_id!r} not found"
            )
        return request

    def _require_pending(self, request_id: str) -> PendingRequest:
        """Return a pending request or raise a conflict for terminal state."""

        request = self._require(request_id)
        if request.status is not PendingRequestStatus.PENDING:
            raise PendingRequestConflictError(
                f"request {request_id!r} is already {request.status.value}"
            )
        return request


def _find_native_decision(
    available: tuple[Any, ...], decision: str
) -> Any | None:
    """Find the exact advertised wire value for a public decision key."""

    for native in available:
        if isinstance(native, str) and native == decision:
            return native
        if isinstance(native, dict) and decision in native and len(native) == 1:
            return native
    return None


def _optional_string(value: Any) -> str | None:
    """Return a non-empty string, otherwise None."""

    return value if isinstance(value, str) and value else None
