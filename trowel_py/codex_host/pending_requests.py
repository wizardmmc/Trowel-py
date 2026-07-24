"""管理连接作用域内由 Codex app-server 发起的 pending request。

app-server request id 只在单个 stdio 连接内唯一。registry 将 id 与 manager 连接
代际组合，绑定所属 Trowel session，并持有 transport handler 等待的一次性 Future。
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping


class PendingRequestKind(str, Enum):
    COMMAND_APPROVAL = "command_approval"
    FILE_APPROVAL = "file_approval"
    UNKNOWN = "unknown"


class PendingRequestStatus(str, Enum):
    PENDING = "pending"
    ANSWERED = "answered"
    EXPIRED = "expired"
    HOST_CLOSED = "host_closed"


class PendingRequestError(Exception):
    pass


class PendingRequestNotFoundError(PendingRequestError):
    pass


class PendingRequestOwnershipError(PendingRequestError):
    pass


class PendingRequestConflictError(PendingRequestError):
    pass


class PendingRequestDecisionError(PendingRequestError):
    pass


@dataclass
class PendingRequest:
    """一个原生 server request 及其一次性响应 Future。"""

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
        """生成 AgentEvent payload，并深拷贝可变的原生 decision。"""

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
    """集中管理连接代际 request identity 与一次性状态转换。"""

    def __init__(self) -> None:
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
        """按连接代际与所属 session 登记 request；缺少 ``threadId`` 时拒绝。"""

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
        return self._requests.get(request_id)

    def list_for_session(self, session_id: str) -> tuple[PendingRequest, ...]:
        """按插入顺序返回 session 的全部保留记录，包括已终止状态。"""

        return tuple(
            request
            for request in self._requests.values()
            if request.session_id == session_id
        )

    def resolve(
        self, session_id: str, request_id: str, decision: str
    ) -> PendingRequest:
        """校验归属与 advertised choice，并用对应原生值一次性完成 Future。"""

        request = self._require(request_id)
        if request.session_id != session_id:
            raise PendingRequestOwnershipError(
                f"request {request_id!r} belongs to another session"
            )
        if request.status is not PendingRequestStatus.PENDING:
            raise PendingRequestConflictError(
                f"request {request_id!r} is already {request.status.value}"
            )
        native_decision = _find_native_decision(request.available_decisions, decision)
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
        """以 fail-closed decision 自动完成 request，绕过 advertised-choice 校验。

        调用者只可用于无法安全展示的 file request、未知 request 等拒绝路径。
        """

        request = self._require_pending(request_id)
        request.status = PendingRequestStatus.ANSWERED
        request.decision = decision
        request.auto_resolved = True
        request.resolution_reason = reason
        request.response.set_result({"decision": decision})
        return request

    def expire(self, request_id: str) -> PendingRequest:
        """将超时 request 标为 expired，并在线上回复 ``decline``。"""

        request = self._require_pending(request_id)
        request.status = PendingRequestStatus.EXPIRED
        request.decision = "decline"
        request.auto_resolved = True
        request.resolution_reason = "request timed out"
        request.response.set_result({"decision": "decline"})
        return request

    def close_generation(self, generation: int) -> tuple[PendingRequest, ...]:
        """取消失效连接代际中仍 pending 的 Future，并标为 host_closed。"""

        return self._close_matching(
            lambda request: request.generation == generation,
            reason="app-server connection closed",
        )

    def close_session(self, session_id: str) -> tuple[PendingRequest, ...]:
        """session 删除时取消仍 pending 的 Future，并标为 host_closed。"""

        return self._close_matching(
            lambda request: request.session_id == session_id,
            reason="session closed",
        )

    def resolve_turn_with_cancel(
        self, session_id: str, turn_id: str
    ) -> tuple[PendingRequest, ...]:
        """原生 interrupt 确认后，以 advertised ``cancel`` 完成该 turn 的审批。"""

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
        closed: list[PendingRequest] = []
        for request in self._requests.values():
            if predicate(request) and request.status is PendingRequestStatus.PENDING:
                request.status = PendingRequestStatus.HOST_CLOSED
                request.resolution_reason = reason
                request.response.cancel()
                closed.append(request)
        return tuple(closed)

    def _require(self, request_id: str) -> PendingRequest:
        request = self._requests.get(request_id)
        if request is None:
            raise PendingRequestNotFoundError(
                f"pending request {request_id!r} not found"
            )
        return request

    def _require_pending(self, request_id: str) -> PendingRequest:
        request = self._require(request_id)
        if request.status is not PendingRequestStatus.PENDING:
            raise PendingRequestConflictError(
                f"request {request_id!r} is already {request.status.value}"
            )
        return request


def _find_native_decision(available: tuple[Any, ...], decision: str) -> Any | None:
    """按公开 choice 找回 app-server advertised 的完整原生值。"""

    for native in available:
        if isinstance(native, str) and native == decision:
            return native
        if isinstance(native, dict) and decision in native and len(native) == 1:
            return native
    return None


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
