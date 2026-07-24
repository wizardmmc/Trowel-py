"""管理共享 Codex app-server，并按 threadId 路由原生通知。

首个请求惰性启动 transport；意外 EOF 会进入 degraded，并向在途 turn 发出
host_exited 终态。transport 只需实现 AppServerClient 协议。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from functools import partial
from typing import Any, Callable, Mapping

from trowel_py.codex_host.catalog import parse_model_list_page
from trowel_py.codex_host.errors import (
    ProtocolViolationError,
    ServerRequestUnsupportedError,
)
from trowel_py.codex_host.events import (
    CodexEventType,
    HostStatusKind,
    TranslatedItem,
    immutable_payload,
)
from trowel_py.codex_host import manager_params
from trowel_py.codex_host.session import CodexSession, ThreadBinding, TurnConflictError
from trowel_py.codex_host.pending_requests import (
    PendingRequest,
    PendingRequestKind,
    PendingRequestRegistry,
)
from trowel_py.codex_host.translator import CodexTranslator
from trowel_py.codex_host.transport import AppServerClient

_log = logging.getLogger(__name__)

# 首次请求可能包含认证检查，因此保留充足的握手时间。
_REQUEST_TIMEOUT_S = 60.0
_PENDING_REQUEST_TIMEOUT_S = 600.0

_COMMAND_APPROVAL_METHOD = "item/commandExecution/requestApproval"
_FILE_APPROVAL_METHOD = "item/fileChange/requestApproval"


class CodexHostManagerState(str, Enum):
    """manager 生命周期。

    Transitions::

        stopped --ensure_ready--> starting --ok--> ready
        ready --EOF--> degraded --ensure_ready--> starting
        any --close--> closing --done--> stopped
    """

    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    DEGRADED = "degraded"
    CLOSING = "closing"


@dataclass(frozen=True)
class OrphanDiagnostic:
    """无法路由的通知诊断。

    orphan 只记录而不抛出，避免阻塞消息总线，也绝不能进入其他会话。
    """

    method: str
    thread_id: str | None
    turn_id: str | None
    reason: str


ClientFactory = Callable[[], AppServerClient]
BeforeTurnStart = Callable[[CodexSession], None]


class CodexHostManager:
    """持有共享 transport 与 thread→session 路由表。"""

    def __init__(
        self,
        *,
        client_factory: ClientFactory | None = None,
        translator: CodexTranslator | None = None,
        pending_request_timeout_s: float = _PENDING_REQUEST_TIMEOUT_S,
    ) -> None:
        self._client_factory: ClientFactory = (
            client_factory or self._default_client_factory
        )
        self._translator: CodexTranslator = translator or CodexTranslator()
        self._client: AppServerClient | None = None
        self._state: CodexHostManagerState = CodexHostManagerState.STOPPED
        self._sessions: dict[str, CodexSession] = {}
        self._thread_to_session: dict[str, CodexSession] = {}
        # 只记录当前连接已加载原生 thread 的本地 session；thread 独占另由
        # _thread_to_session 保证，重连后 attachment 必须重建。
        self._attached_session_ids: set[str] = set()
        self._orphans: list[OrphanDiagnostic] = []
        self._ready_lock: asyncio.Lock = asyncio.Lock()
        self._eof_watcher: asyncio.Task[None] | None = None
        self._pending_requests = PendingRequestRegistry()
        self._pending_request_timeout_s = pending_request_timeout_s
        self._connection_generation = 0
        self._active_generation = 0

    @property
    def state(self) -> CodexHostManagerState:
        return self._state

    @property
    def client(self) -> AppServerClient | None:
        return self._client

    @property
    def orphans(self) -> list[OrphanDiagnostic]:
        return list(self._orphans)

    @property
    def translator(self) -> CodexTranslator:
        return self._translator

    @property
    def connection_generation(self) -> int:
        return self._active_generation

    def register(self, session: CodexSession) -> None:
        """注册本地 session（同 id 覆盖）；thread 路由由 send 的挂载流程管理。"""

        self._sessions[session.session_id] = session

    def get_session(self, session_id: str) -> CodexSession | None:
        return self._sessions.get(session_id)

    @property
    def session_ids(self) -> tuple[str, ...]:
        return tuple(self._sessions.keys())

    def unregister(self, session_id: str) -> CodexSession | None:
        """注销本地 session、关闭其 pending request 并移除路由，不删除原生 thread。"""

        session = self._sessions.pop(session_id, None)
        for request in self._pending_requests.close_session(session_id):
            if session is not None:
                self._emit_request_event(session, request)
        self._attached_session_ids.discard(session_id)
        if session is not None and session.binding is not None:
            self._thread_to_session.pop(session.binding.thread_id, None)
        return session

    def session_for_thread(self, thread_id: str) -> CodexSession | None:
        return self._thread_to_session.get(thread_id)

    def _require_registered(self, session: CodexSession) -> None:
        """拒绝跨 await 期间已被删除或替换的 session。"""

        if self._sessions.get(session.session_id) is not session:
            raise TurnConflictError(
                f"session {session.session_id} is no longer registered"
            )

    async def ensure_ready(self) -> AppServerClient:
        """串行化惰性启动；每次成功建连（含首次）都广播 READY。"""

        async with self._ready_lock:
            if (
                self._state is CodexHostManagerState.READY
                and self._client is not None
                and not self._client.closed
            ):
                return self._client
            self._state = CodexHostManagerState.STARTING
            # binding 跨连接保留，app-server 内存中的 attachment 不保留。
            self._attached_session_ids.clear()
            client = self._client_factory()
            self._connection_generation += 1
            generation = self._connection_generation
            self._active_generation = generation
            client.register_server_request_handler(
                _COMMAND_APPROVAL_METHOD,
                partial(self._handle_server_request, generation),
            )
            client.register_server_request_handler(
                _FILE_APPROVAL_METHOD,
                partial(self._handle_server_request, generation),
            )
            client.register_unknown_server_request_handler(
                partial(self._handle_unknown_server_request, generation)
            )
            # 握手前先安装新 identity，上一代迟到的 EOF 才不会降级新连接。
            self._client = client
            try:
                await client.start()
            except BaseException:
                if self._client is client:
                    self._client = None
                    self._state = CodexHostManagerState.DEGRADED
                raise
            client.add_notification_listener(self._on_notification)
            self._state = CodexHostManagerState.READY
            self._broadcast_host_status(HostStatusKind.READY, reason="ready")
            self._eof_watcher = asyncio.create_task(
                self._eof_watcher_loop(), name="codex-host-eof-watcher"
            )
            return client

    async def close(self) -> None:
        self._state = CodexHostManagerState.CLOSING
        self._close_generation_requests(
            self._active_generation, reason="app-server manager closed"
        )
        client = self._client
        watcher = self._eof_watcher
        self._eof_watcher = None
        if client is not None:
            await client.close()
        if watcher is not None and not watcher.done():
            watcher.cancel()
            try:
                await watcher
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 — 关闭流程只记录 watcher 异常
                _log.debug("eof watcher raised during close", exc_info=True)
        self._client = None
        self._attached_session_ids.clear()
        self._state = CodexHostManagerState.STOPPED

    async def list_models(self) -> list[dict[str, Any]]:
        """翻页返回全部可见模型，保留原生顺序与未知枚举值。"""

        client = await self.ensure_ready()
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {"includeHidden": False}
            if cursor is not None:
                params["cursor"] = cursor
            result = await client.request(
                "model/list", params, timeout=_REQUEST_TIMEOUT_S
            )
            page, cursor = parse_model_list_page(result)
            rows.extend(page)
            if cursor is None:
                return rows

    async def list_threads(self, *, cwd: str, limit: int) -> list[dict[str, Any]]:
        """按更新时间列出指定 cwd 的默认交互 thread，不读取私有 rollout。"""

        if limit <= 0:
            return []
        client = await self.ensure_ready()
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while len(rows) < limit:
            params: dict[str, Any] = {
                "cwd": cwd,
                "limit": limit - len(rows),
                "sortKey": "updated_at",
                "sortDirection": "desc",
            }
            if cursor is not None:
                params["cursor"] = cursor
            result = await client.request(
                "thread/list", params, timeout=_REQUEST_TIMEOUT_S
            )
            data = result.get("data") if isinstance(result, Mapping) else None
            if not isinstance(data, list) or not all(
                isinstance(row, Mapping) for row in data
            ):
                raise ProtocolViolationError("thread/list result.data is not an array")
            rows.extend(dict(row) for row in data[: limit - len(rows)])

            next_cursor = result.get("nextCursor")
            if next_cursor is not None and not isinstance(next_cursor, str):
                raise ProtocolViolationError(
                    "thread/list nextCursor is not a string or null"
                )
            if next_cursor is None or len(rows) >= limit:
                return rows
            if next_cursor in seen_cursors:
                raise ProtocolViolationError("thread/list returned a repeated cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor

    async def read_thread(self, thread_id: str) -> dict[str, Any]:
        """通过公共 ``thread/read`` 取得含 turns 的 transcript。"""

        client = await self.ensure_ready()
        result = await client.request(
            "thread/read",
            {"threadId": thread_id, "includeTurns": True},
            timeout=_REQUEST_TIMEOUT_S,
        )
        thread = result.get("thread") if isinstance(result, Mapping) else None
        if not isinstance(thread, Mapping):
            raise ProtocolViolationError("thread/read result.thread is not an object")
        return dict(thread)

    async def attach(self, session: CodexSession) -> ThreadBinding:
        """按当前连接代际 start/resume thread，但不启动 turn。"""

        self._require_registered(session)
        client = await self.ensure_ready()
        self._require_registered(session)
        if session.session_id in self._attached_session_ids:
            binding = session.binding
            if binding is None:
                raise ProtocolViolationError("attached session has no thread binding")
            return binding
        reserved_thread_id: str | None = None
        binding = session.binding
        try:
            if binding is not None:
                owner = self._thread_to_session.get(binding.thread_id)
                if owner is None:
                    self._thread_to_session[binding.thread_id] = session
                    reserved_thread_id = binding.thread_id
                elif owner is not session:
                    raise TurnConflictError(
                        f"thread {binding.thread_id} is already attached to "
                        f"session {owner.session_id}"
                    )
            if session.is_new_thread:
                result = await client.request(
                    "thread/start",
                    self._thread_start_params(session),
                    timeout=_REQUEST_TIMEOUT_S,
                )
            else:
                result = await client.request(
                    "thread/resume",
                    self._thread_resume_params(session),
                    timeout=_REQUEST_TIMEOUT_S,
                )
            self._require_registered(session)
            attached = session.attach_thread_binding(result)
            session.emit_session_started_if_first()
            self._attached_session_ids.add(session.session_id)
            self._thread_to_session[attached.thread_id] = session
            return attached
        except BaseException:
            if (
                reserved_thread_id is not None
                and self._thread_to_session.get(reserved_thread_id) is session
            ):
                self._thread_to_session.pop(reserved_thread_id, None)
            raise

    async def send(
        self,
        session: CodexSession,
        text: str,
        *,
        before_turn_start: BeforeTurnStart | None = None,
    ) -> str:
        """执行一个 turn：确保连接、按需挂载 thread，再启动原生 turn。

        同一 session 的 thread 在每个连接代际只 start/resume 一次，后续
        turn 复用已加载的 thread。
        ``before_turn_start`` 在挂载后、原生工作前同步执行，确保持久化
        失败时不会留下失去追踪的 turn。返回原生 ``turn_id``。
        """

        self._require_registered(session)
        session.begin_send()
        try:
            await self.attach(session)
            client = await self.ensure_ready()
            assert session.binding is not None
            self._require_registered(session)
            self._thread_to_session[session.binding.thread_id] = session
            if before_turn_start is not None:
                before_turn_start(session)
            self._require_registered(session)
            model, effort = session.next_turn_settings()
            turn_result = await client.request(
                "turn/start",
                self._turn_start_params(
                    session.binding.thread_id,
                    text,
                    model=model,
                    effort=effort,
                ),
                timeout=_REQUEST_TIMEOUT_S,
            )
            turn_id = _extract_turn_id(turn_result)
            try:
                self._require_registered(session)
            except TurnConflictError:
                # 仅处理 session 在 turn/start await 中被删除的竞态：拿到
                # turn_id 说明原生端已接收，必须中断已无消费者的隐形任务。
                try:
                    await client.request(
                        "turn/interrupt",
                        {"threadId": session.binding.thread_id, "turnId": turn_id},
                        timeout=_REQUEST_TIMEOUT_S,
                    )
                except Exception:  # noqa: BLE001 — 保留原注册状态错误
                    _log.warning(
                        "failed to interrupt turn %s for deleted session %s",
                        turn_id,
                        session.session_id,
                        exc_info=True,
                    )
                raise
            session.commit_turn_settings(model=model, effort=effort)
            session.record_turn_started(turn_id, text)
            return turn_id
        except BaseException:
            # 所有失败都需释放 _sending，成功路径由 record_turn_started 清除。
            session.abort_send()
            raise

    async def interrupt(self, session: CodexSession) -> None:
        """请求中断当前 turn；终态仍以原生 ``turn/completed.status`` 为准。"""

        binding = session.binding
        turn_id = session.current_turn_id
        if binding is None or turn_id is None:
            return
        client = await self.ensure_ready()
        await client.request(
            "turn/interrupt",
            {"threadId": binding.thread_id, "turnId": turn_id},
            timeout=_REQUEST_TIMEOUT_S,
        )
        # 原生中断确认后再取消审批；反序会让 turn 先结束，中断请求无法抵达。
        for request in self._pending_requests.resolve_turn_with_cancel(
            session.session_id, turn_id
        ):
            self._emit_request_event(session, request)

    def answer_request(
        self, session_id: str, request_id: str, decision: str
    ) -> PendingRequest:
        """校验归属与决策后，一次性解决待处理审批。"""

        request = self._pending_requests.resolve(session_id, request_id, decision)
        session = self._sessions.get(session_id)
        if session is not None:
            self._emit_request_event(session, request)
        return request

    def list_requests(self, session_id: str) -> tuple[PendingRequest, ...]:
        return self._pending_requests.list_for_session(session_id)

    async def _handle_server_request(
        self,
        generation: int,
        native_request_id: Any,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """登记已验证归属的审批，并等待一次性答复。"""

        session = self._request_session(generation, method, native_request_id, params)
        kind = (
            PendingRequestKind.COMMAND_APPROVAL
            if method == _COMMAND_APPROVAL_METHOD
            else PendingRequestKind.FILE_APPROVAL
        )
        request = self._pending_requests.create(
            native_request_id=native_request_id,
            generation=generation,
            session_id=session.session_id,
            kind=kind,
            params=params,
        )
        if kind is PendingRequestKind.FILE_APPROVAL:
            self._pending_requests.resolve_automatically(
                request.request_id,
                "decline",
                reason="request omitted path, diff, and available decisions",
            )
            self._emit_request_event(session, request)
            return await request.response

        self._emit_request_event(session, request)
        try:
            return await asyncio.wait_for(
                asyncio.shield(request.response),
                timeout=self._pending_request_timeout_s,
            )
        except asyncio.TimeoutError:
            expired = self._pending_requests.expire(request.request_id)
            self._emit_request_event(session, expired)
            return await expired.response

    async def _handle_unknown_server_request(
        self,
        generation: int,
        native_request_id: Any,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """向所属 session 暴露未知请求，再安全拒绝。"""

        try:
            session = self._request_session(
                generation, method, native_request_id, params
            )
        except ServerRequestUnsupportedError:
            raise
        request = self._pending_requests.create(
            native_request_id=native_request_id,
            generation=generation,
            session_id=session.session_id,
            kind=PendingRequestKind.UNKNOWN,
            params=params,
        )
        self._pending_requests.resolve_automatically(
            request.request_id,
            "unsupported",
            reason=f"unsupported server request method {method}",
        )
        self._emit_request_event(session, request)
        raise ServerRequestUnsupportedError(method, native_request_id)

    def _request_session(
        self,
        generation: int,
        method: str,
        native_request_id: Any,
        params: Mapping[str, Any],
    ) -> CodexSession:
        """只在当前连接代际内解析请求归属。"""

        if generation != self._active_generation:
            raise ServerRequestUnsupportedError(method, native_request_id)
        thread_id = _extract_thread_id(params)
        session = self._thread_to_session.get(thread_id or "")
        if session is None:
            raise ServerRequestUnsupportedError(method, native_request_id)
        return session

    @staticmethod
    def _emit_request_event(
        session: CodexSession, request: PendingRequest
    ) -> None:
        session.emit_translated(
            TranslatedItem(
                type=CodexEventType.APPROVAL_REQUEST,
                thread_id=request.thread_id,
                turn_id=request.turn_id,
                item_id=request.item_id,
                payload=immutable_payload(**request.to_payload()),
            )
        )

    def _close_generation_requests(self, generation: int, *, reason: str) -> None:
        """关闭失效连接代际的全部审批，并通知原所属 session。"""

        if generation <= 0:
            return
        for request in self._pending_requests.close_generation(generation):
            request.resolution_reason = reason
            session = self._sessions.get(request.session_id)
            if session is not None:
                self._emit_request_event(session, request)

    def _on_notification(self, method: str, params: Mapping[str, Any]) -> None:
        """在 transport reader 上同步路由，不能阻塞；下游入队均为非阻塞操作。"""

        if method in self._translator.ignored_methods:
            return  # 能力门控或回显，无需分发
        if method in self._translator.account_level_methods:
            self._dispatch_account_level(method, params)
            return
        thread_id = _extract_thread_id(params)
        if thread_id is None:
            self._record_orphan(
                method, None, _extract_turn_id_from_params(params), "no_thread_id"
            )
            return
        session = self._thread_to_session.get(thread_id)
        if session is None:
            self._record_orphan(
                method,
                thread_id,
                _extract_turn_id_from_params(params),
                "unknown_thread",
            )
            return
        try:
            items = self._translator.translate(method, params)
        except ProtocolViolationError as exc:
            # 已映射协议发生漂移时向所属 session 报错，但不能杀死 reader。
            _log.warning("translator rejected %s: %s", method, exc)
            session.emit_translated(
                TranslatedItem(
                    type=CodexEventType.ERROR,
                    thread_id=thread_id,
                    turn_id=_extract_turn_id_from_params(params),
                    payload=immutable_payload(
                        kind="translator_error",
                        method=method,
                        message=str(exc),
                    ),
                )
            )
            return
        if not items:
            # 非忽略方法未产出事件时留诊断，避免协议变化被静默丢弃。
            self._record_orphan(
                method,
                thread_id,
                _extract_turn_id_from_params(params),
                "unknown_method",
            )
            return
        for item in items:
            session.emit_translated(item)

    def _dispatch_account_level(
        self, method: str, params: Mapping[str, Any]
    ) -> None:
        """翻译无 ``threadId`` 的账户级通知，并广播给全部已注册 session。

        此类通知没有唯一归属；协议错误只记录日志，避免污染所有事件队列。
        """

        try:
            items = self._translator.translate(method, params)
        except ProtocolViolationError as exc:
            _log.warning("translator rejected account-level %s: %s", method, exc)
            return
        if not items:
            return
        for session in self._sessions.values():
            for item in items:
                session.emit_translated(item)

    def _record_orphan(
        self, method: str, thread_id: str | None, turn_id: str | None, reason: str
    ) -> None:
        diag = OrphanDiagnostic(
            method=method, thread_id=thread_id, turn_id=turn_id, reason=reason
        )
        self._orphans.append(diag)
        _log.debug(
            "codex orphan notification: method=%s thread=%s turn=%s reason=%s",
            method,
            thread_id,
            turn_id,
            reason,
        )

    async def _eof_watcher_loop(self) -> None:
        """等待 transport 关闭；主动关闭不广播 degraded。"""

        client = self._client
        if client is None:
            return
        try:
            await client.wait_closed()
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001 — 防御未声明的 transport 异常
            _log.debug("wait_closed raised", exc_info=True)
            return
        if self._state is CodexHostManagerState.CLOSING:
            return
        await self._on_unexpected_exit(client)

    async def _on_unexpected_exit(self, client: AppServerClient) -> None:
        """进入 degraded，并终止所有仍在途的 turn。"""

        if client is not self._client:
            # 旧 watcher 可能晚于新连接返回，不能让陈旧 EOF 降级当前连接。
            _log.debug("ignoring stale codex host exit")
            return
        exit_code = client.last_exit_code
        stderr_tail = client.stderr_tail[:200] if client else ""
        self._state = CodexHostManagerState.DEGRADED
        self._client = None
        self._attached_session_ids.clear()
        self._eof_watcher = None
        self._close_generation_requests(
            self._active_generation, reason="app-server process exited"
        )
        reason = "app-server process exited unexpectedly"
        if stderr_tail:
            reason = f"{reason}; stderr={stderr_tail!r}"
        for session in self._sessions.values():
            # 在途包括 begin_send 到 record_turn_started 的窗口；二者都需
            # HOST_EXITED 释放终态和发送占位，空闲 session 只接收状态变化。
            if session.has_in_flight_turn:
                session.mark_host_exited(reason, exit_code=exit_code)
            else:
                session.emit_host_status(HostStatusKind.DEGRADED, reason=reason)
        _log.warning("codex host degraded: %s (exit_code=%s)", reason, exit_code)

    def _broadcast_host_status(
        self, status: HostStatusKind, *, reason: str | None
    ) -> None:
        for session in self._sessions.values():
            session.emit_host_status(status, reason=reason)

    @staticmethod
    def _default_client_factory() -> AppServerClient:
        return AppServerClient()

    def _thread_start_params(self, session: CodexSession) -> dict[str, Any]:
        return manager_params.thread_start_params(session)

    def _thread_resume_params(self, session: CodexSession) -> dict[str, Any]:
        return manager_params.thread_resume_params(session)

    @staticmethod
    def _turn_start_params(
        thread_id: str,
        text: str,
        *,
        model: str | None = None,
        effort: str | None = None,
    ) -> dict[str, Any]:
        return manager_params.turn_start_params(
            thread_id,
            text,
            model=model,
            effort=effort,
        )


def _extract_thread_id(params: Mapping[str, Any]) -> str | None:
    """只读取通知顶层 ``threadId``。

    全局通知和 ``thread/started`` 都返回 ``None``；前者由上游分支广播，
    后者由 translator 的忽略列表处理。
    """

    value = params.get("threadId")
    if isinstance(value, str) and value:
        return value
    return None


def _extract_turn_id_from_params(params: Mapping[str, Any]) -> str | None:
    value = params.get("turnId")
    return value if isinstance(value, str) and value else None


def _extract_turn_id(turn_result: Mapping[str, Any]) -> str:
    """提取通知路由所需的 ``turn.id``；缺失即表示协议漂移。"""

    turn = turn_result.get("turn")
    if not isinstance(turn, Mapping) or not turn.get("id"):
        raise ProtocolViolationError(
            "turn/start response has no turn.id",
            payload=dict(turn_result),
        )
    return str(turn["id"])
