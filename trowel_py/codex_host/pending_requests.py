"""管理 app-server 发起、等待 Trowel 答复的一次性请求。

原生请求 ID 只在单次连接中唯一，registry 将连接代际与原始 ID 组合为公开 ID，并将
请求绑定到所属会话。每个请求通过一个 Future 向 transport handler 返回结果；结束后
记录仍保留，用于查询和发出最终状态事件。
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Mapping


class PendingRequestKind(str, Enum):
    """区分命令审批、文件审批和不支持的服务端请求。"""

    COMMAND_APPROVAL = "command_approval"
    FILE_APPROVAL = "file_approval"
    UNKNOWN = "unknown"


class PendingRequestStatus(str, Enum):
    """表示请求从 pending 单向进入回答、超时或连接/会话关闭终态。"""

    PENDING = "pending"
    ANSWERED = "answered"
    EXPIRED = "expired"
    HOST_CLOSED = "host_closed"


class PendingRequestError(Exception):
    """待决请求处理失败的基类。"""

    pass


class PendingRequestNotFoundError(PendingRequestError):
    """指定的待决请求不存在。"""

    pass


class PendingRequestOwnershipError(PendingRequestError):
    """待决请求不属于当前 Trowel 会话。"""

    pass


class PendingRequestConflictError(PendingRequestError):
    """公开请求 ID 重复、请求已结束或操作与当前状态冲突。"""

    pass


class PendingRequestDecisionError(PendingRequestError):
    """决策不在该请求声明的可选值中。"""

    pass


@dataclass
class PendingRequest:
    """一个原生服务端请求及其一次性响应 Future。

    Attributes:
        request_id: 由连接代际和原始请求 ID 组成的 Trowel 公开 ID。
        native_request_id: app-server 在当前连接中分配的原始请求 ID。
        generation: 接收请求的 manager 连接代际。
        session_id: 请求所属的 Trowel 会话 ID。
        thread_id: 请求所属的 Codex thread ID。
        turn_id: 请求关联的 Codex turn ID；未提供时为 None。
        item_id: 请求关联的 Codex item ID；未提供时为 None。
        kind: 请求类型。
        available_decisions: app-server 声明的原生决策值副本。
        command: 待审批命令；原生请求未提供时为 None。
        cwd: 命令工作目录；原生请求未提供时为 None。
        reason: app-server 提供的审批原因；未提供时为 None。
        response: transport handler 等待的 Future；可由决策结果完成，也可在连接或
            会话关闭时取消。
        status: 当前请求状态。
        decision: Trowel 采用的公开决策名；pending 或连接/会话关闭取消时为 None。
        auto_resolved: 是否由超时或拒绝策略自动选择了决策；连接或会话关闭取消
            Future 时仍为 False。
        resolution_reason: 自动结束或 Host 关闭的原因；没有时为 None。
    """

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
        """生成审批事件 payload，并深拷贝可变的原生决策列表。"""

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
    """管理请求的创建和单向状态转换，并保留所属会话的终态记录。"""

    def __init__(self) -> None:
        """初始化按插入顺序保存且不会自动淘汰终态记录的内存表。"""

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
        """登记请求，复制原生决策，并创建 transport handler 等待的 Future。

        公开 ID 由 ``generation`` 和 ``native_request_id`` 组成。缺少非空
        ``threadId`` 或公开 ID 已存在时拒绝创建。
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
        """按公开请求 ID 查找记录，包括已进入终态的记录。"""

        return self._requests.get(request_id)

    def list_for_session(self, session_id: str) -> tuple[PendingRequest, ...]:
        """按插入顺序返回会话的全部保留记录，包括已进入终态的记录。"""

        return tuple(
            request
            for request in self._requests.values()
            if request.session_id == session_id
        )

    def resolve(
        self, session_id: str, request_id: str, decision: str
    ) -> PendingRequest:
        """校验会话归属和公开决策名，再用对应原生值完成 Future。

        结构化决策会原样返回给 app-server；非 pending 请求不能再次回答。
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
        """绕过可选值校验，以调用方给定的拒绝决策自动完成请求。

        仅用于无法交给用户判断的文件审批或不支持请求；调用方必须提供 decline、
        unsupported 等拒绝结果及原因。
        """

        request = self._require_pending(request_id)
        request.status = PendingRequestStatus.ANSWERED
        request.decision = decision
        request.auto_resolved = True
        request.resolution_reason = reason
        request.response.set_result({"decision": decision})
        return request

    def expire(self, request_id: str) -> PendingRequest:
        """将超时请求标为 expired，并用 ``decline`` 完成响应 Future。"""

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
        """会话删除时取消其 pending Future，并标为 host_closed。"""

        return self._close_matching(
            lambda request: request.session_id == session_id,
            reason="session closed",
        )

    def resolve_turn_with_cancel(
        self, session_id: str, turn_id: str
    ) -> tuple[PendingRequest, ...]:
        """原生中断确认后，用名为 ``cancel`` 的完整原生值回答该 turn 的 pending 审批。"""

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
        """取消符合条件的 pending Future，并保留标为 host_closed 的记录。"""

        closed: list[PendingRequest] = []
        for request in self._requests.values():
            if predicate(request) and request.status is PendingRequestStatus.PENDING:
                request.status = PendingRequestStatus.HOST_CLOSED
                request.resolution_reason = reason
                request.response.cancel()
                closed.append(request)
        return tuple(closed)

    def _require(self, request_id: str) -> PendingRequest:
        """返回指定记录，不存在时抛出 ``PendingRequestNotFoundError``。"""

        request = self._requests.get(request_id)
        if request is None:
            raise PendingRequestNotFoundError(
                f"pending request {request_id!r} not found"
            )
        return request

    def _require_pending(self, request_id: str) -> PendingRequest:
        """返回 pending 记录；不存在或已结束时分别抛出对应领域错误。"""

        request = self._require(request_id)
        if request.status is not PendingRequestStatus.PENDING:
            raise PendingRequestConflictError(
                f"request {request_id!r} is already {request.status.value}"
            )
        return request


def _find_native_decision(available: tuple[Any, ...], decision: str) -> Any | None:
    """按公开决策名找回原生字符串或以该名称为唯一键的对象。"""

    for native in available:
        if isinstance(native, str) and native == decision:
            return native
        if isinstance(native, dict) and decision in native and len(native) == 1:
            return native
    return None


def _optional_string(value: Any) -> str | None:
    """只保留非空字符串，其他输入统一视为缺失。"""

    return value if isinstance(value, str) and value else None
