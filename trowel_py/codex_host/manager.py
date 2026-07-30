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
from trowel_py.codex_host.child_threads import ChildThreadRegistry
from trowel_py.codex_host.commands import command_roster
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

    除 ``ready`` 且 client 仍打开的情况外，``ensure_ready`` 都会发起新连接。

    Transitions::

        non-usable --ensure_ready--> starting --success--> ready
        starting --failure--> degraded
        ready --unexpected close--> degraded
        any --close--> closing --success--> stopped
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

    Attributes:
        method: 原生通知方法名。
        thread_id: 通知携带的 Codex thread ID；缺失时为 None。
        turn_id: 通知携带的 Codex turn ID；缺失时为 None。
        reason: 无法路由的稳定原因码。
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
        """初始化共享连接、会话路由和待决请求登记表。

        Args:
            client_factory: app-server client 工厂；省略时创建真实 ``AppServerClient``。
            translator: 原生通知翻译器；省略时创建无状态实例。
            pending_request_timeout_s: 命令审批等待答复的秒数。
        """

        self._client_factory: ClientFactory = (
            client_factory or self._default_client_factory
        )
        self._translator: CodexTranslator = translator or CodexTranslator()
        self._client: AppServerClient | None = None
        self._state: CodexHostManagerState = CodexHostManagerState.STOPPED
        self._sessions: dict[str, CodexSession] = {}
        self._thread_to_session: dict[str, CodexSession] = {}
        self._child_threads = ChildThreadRegistry()
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
        """返回共享 Codex 进程的当前生命周期状态。"""

        return self._state

    @property
    def client(self) -> AppServerClient | None:
        """返回当前 Codex 连接；尚未启动或连接已失效时为空。"""

        return self._client

    @property
    def orphans(self) -> list[OrphanDiagnostic]:
        """返回无法归属到会话的通知诊断副本。"""

        return list(self._orphans)

    @property
    def translator(self) -> CodexTranslator:
        """返回当前使用的 Codex 事件翻译器。"""

        return self._translator

    @property
    def connection_generation(self) -> int:
        """返回最近一次连接启动尝试的代际编号；尚未尝试时为 0。"""

        return self._active_generation

    def register(self, session: CodexSession) -> None:
        """登记本地 session；同 ID 会替换登记项，但不会改写已有 thread 路由。"""

        self._sessions[session.session_id] = session

    def get_session(self, session_id: str) -> CodexSession | None:
        """按 Trowel 会话 ID 查找 Codex 会话。"""

        return self._sessions.get(session_id)

    @property
    def session_ids(self) -> tuple[str, ...]:
        """按注册顺序返回全部 Trowel 会话 ID。"""

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
        for thread_id in self._child_threads.remove_session(session) if session else ():
            self._thread_to_session.pop(thread_id, None)
        return session

    def session_for_thread(self, thread_id: str) -> CodexSession | None:
        """查找当前负责指定 Codex 线程的 Trowel 会话。"""

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
        """依次关闭共享连接与监听任务，再清除本次连接的挂载状态。

        client 关闭异常会直接向上传播，此时后续清理不会执行，状态保持 ``closing``。
        """

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

    async def list_commands(self) -> list[dict[str, Any]]:
        """按当前已连接的 CLI 版本返回经过验证的命令能力。"""

        client = await self.ensure_ready()
        version = str(client.version) if client.version is not None else None
        return command_roster(version)

    async def list_threads(
        self,
        *,
        cwd: str,
        limit: int,
        excluded_ids: frozenset[str] = frozenset(),
    ) -> list[dict[str, Any]]:
        """按更新时间列出指定 cwd 的默认交互 thread。

        不读取私有 rollout。已确认属于 Trowel 委派子会话的 thread 在原生分页期间
        排除，因此返回数量和后续合并分页不会被内部会话占用。

        Args:
            cwd: 只读取该工作目录下的 Codex thread。
            limit: 最多返回的非排除 thread 数。
            excluded_ids: 已确认属于 Trowel 委派子会话的 Codex thread ID。
        """

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
            for row in data:
                thread_id = row.get("id")
                if isinstance(thread_id, str) and thread_id in excluded_ids:
                    continue
                rows.append(dict(row))
                if len(rows) >= limit:
                    break

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

    async def get_goal(self, session: CodexSession) -> dict[str, Any] | None:
        """读取当前会话绑定线程的目标。"""

        binding = await self.attach(session)
        client = await self.ensure_ready()
        result = await client.request(
            "thread/goal/get",
            {"threadId": binding.thread_id},
            timeout=_REQUEST_TIMEOUT_S,
        )
        goal = result.get("goal") if isinstance(result, Mapping) else None
        if goal is None:
            return None
        if not isinstance(goal, Mapping):
            raise ProtocolViolationError("thread/goal/get result.goal is not an object")
        return dict(goal)

    async def set_goal(
        self,
        session: CodexSession,
        *,
        objective: str | None = None,
        status: str | None = None,
        token_budget: int | None = None,
        token_budget_supplied: bool = False,
    ) -> dict[str, Any]:
        """更新当前会话绑定线程的目标字段并返回完整目标。

        Args:
            session: 目标所属的 Trowel 会话。
            objective: 新目标正文；None 表示不更新。
            status: 新目标状态；None 表示不更新。
            token_budget: 新 token 预算；可在显式提供时传 None 清空预算。
            token_budget_supplied: 是否将 ``token_budget`` 写入请求，用于区分省略字段和
                显式清空。

        Returns:
            app-server 更新后的完整 goal 对象。
        """

        binding = await self.attach(session)
        client = await self.ensure_ready()
        params: dict[str, Any] = {"threadId": binding.thread_id}
        if objective is not None:
            params["objective"] = objective
        if status is not None:
            params["status"] = status
        if token_budget_supplied:
            params["tokenBudget"] = token_budget
        result = await client.request(
            "thread/goal/set", params, timeout=_REQUEST_TIMEOUT_S
        )
        goal = result.get("goal") if isinstance(result, Mapping) else None
        if not isinstance(goal, Mapping):
            raise ProtocolViolationError("thread/goal/set result.goal is not an object")
        return dict(goal)

    async def clear_goal(self, session: CodexSession) -> bool:
        """清除当前会话绑定线程的目标。"""

        binding = await self.attach(session)
        client = await self.ensure_ready()
        result = await client.request(
            "thread/goal/clear",
            {"threadId": binding.thread_id},
            timeout=_REQUEST_TIMEOUT_S,
        )
        cleared = result.get("cleared") if isinstance(result, Mapping) else None
        if not isinstance(cleared, bool):
            raise ProtocolViolationError("thread/goal/clear result.cleared is not boolean")
        return cleared

    async def compact(
        self,
        session: CodexSession,
        *,
        before_start: BeforeTurnStart | None = None,
    ) -> None:
        """在空闲 thread 上启动原生压缩，并与 turn 启动共享会话预留。

        ``before_start`` 在 thread 挂载后、请求压缩前同步执行；回调或请求失败都会
        释放会话预留。
        """

        self._require_registered(session)
        session.begin_send(autonomous=True, memory_eligible=False)
        try:
            binding = await self.attach(session)
            if before_start is not None:
                before_start(session)
            client = await self.ensure_ready()
            await client.request(
                "thread/compact/start",
                {"threadId": binding.thread_id},
                timeout=_REQUEST_TIMEOUT_S,
            )
        except BaseException:
            session.abort_send()
            raise

    async def start_review(
        self,
        session: CodexSession,
        target: Mapping[str, Any],
        *,
        before_start: BeforeTurnStart | None = None,
    ) -> dict[str, str]:
        """启动 inline 原生 review，并登记不含 USER 事件的自主 turn。

        review 必须复用当前 thread；返回值同时包含确认后的 thread ID 和 turn ID。
        ``before_start`` 在 thread 挂载后、原生 review 启动前同步执行。
        """

        self._require_registered(session)
        session.begin_send(memory_eligible=False)
        try:
            binding = await self.attach(session)
            if before_start is not None:
                before_start(session)
            client = await self.ensure_ready()
            result = await client.request(
                "review/start",
                {
                    "threadId": binding.thread_id,
                    "target": dict(target),
                    "delivery": "inline",
                },
                timeout=_REQUEST_TIMEOUT_S,
            )
            turn_id = _extract_turn_id(result)
            review_thread_id = (
                result.get("reviewThreadId") if isinstance(result, Mapping) else None
            )
            if not isinstance(review_thread_id, str) or not review_thread_id:
                raise ProtocolViolationError(
                    "review/start response has no reviewThreadId",
                    payload=dict(result),
                )
            if review_thread_id != binding.thread_id:
                raise ProtocolViolationError(
                    "inline review/start returned a different reviewThreadId",
                    payload=dict(result),
                )
            session.record_autonomous_turn_started(turn_id)
            return {
                "review_thread_id": review_thread_id,
                "turn_id": turn_id,
            }
        except BaseException:
            session.abort_send()
            raise

    async def attach(self, session: CodexSession) -> ThreadBinding:
        """在当前连接中 start 或 resume thread，但不启动 turn。

        同一会话在一个连接代际内只加载一次；已有 thread 不能同时归属其他会话。
        """

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
            approval, sandbox = session.next_turn_permission_override()
            turn_result = await client.request(
                "turn/start",
                self._turn_start_params(
                    session.binding.thread_id,
                    text,
                    model=model,
                    effort=effort,
                    approval=approval,
                    sandbox=sandbox,
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
        """校验会话归属和决策值后，一次性解决待处理审批。"""

        request = self._pending_requests.resolve(session_id, request_id, decision)
        session = self._sessions.get(session_id)
        if session is not None:
            self._emit_request_event(session, request)
        return request

    def list_requests(self, session_id: str) -> tuple[PendingRequest, ...]:
        """返回指定会话保留的全部待决请求记录。"""

        return self._pending_requests.list_for_session(session_id)

    async def _handle_server_request(
        self,
        generation: int,
        native_request_id: Any,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """登记已验证归属的审批，并等待一次性答复。

        所有 ``fileChange`` 审批当前都立即返回 decline；该上游请求结构没有提供路径、
        diff 或可选决策，无法交给用户判断。命令审批等待用户答复，超时后由登记表
        生成拒绝结果。
        """

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
        """若能解析归属则发送 unsupported 事件，随后抛出不支持请求错误。"""

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
        """将待决请求的当前状态发到所属会话。"""

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
        if method == "turn/started":
            turn = params.get("turn")
            turn_id = turn.get("id") if isinstance(turn, Mapping) else None
            if not isinstance(turn_id, str) or not turn_id:
                self._record_orphan(method, thread_id, None, "missing_turn_id")
                return
            try:
                if self._child_threads.is_child(thread_id):
                    session.emit_child_translated(
                        TranslatedItem(
                            type=CodexEventType.TURN_STARTED,
                            thread_id=thread_id,
                            turn_id=turn_id,
                            payload=immutable_payload(
                                autonomous=True,
                                memory_eligible=False,
                            ),
                        )
                    )
                else:
                    session.record_native_turn_started(turn_id)
            except TurnConflictError as exc:
                _log.warning("native turn start rejected for %s: %s", thread_id, exc)
            return
        try:
            items = self._translator.translate(method, params)
        except ProtocolViolationError as exc:
            # 已映射协议发生漂移时向所属 session 报错，但不能杀死 reader。
            _log.warning("translator rejected %s: %s", method, exc)
            error_item = TranslatedItem(
                type=CodexEventType.ERROR,
                thread_id=thread_id,
                turn_id=_extract_turn_id_from_params(params),
                payload=immutable_payload(
                    kind="translator_error",
                    method=method,
                    message=str(exc),
                ),
            )
            if self._child_threads.is_child(thread_id):
                session.emit_child_translated(error_item)
            else:
                session.emit_translated(error_item)
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
            if item.type is CodexEventType.SUBAGENT_ACTIVITY:
                child_thread_id = item.payload.get("agent_thread_id")
                if isinstance(child_thread_id, str) and child_thread_id:
                    accepted = self._child_threads.register(
                        thread_id=child_thread_id,
                        parent_thread_id=thread_id,
                        session=session,
                    )
                    if not accepted:
                        self._record_orphan(
                            method,
                            child_thread_id,
                            item.turn_id,
                            "child_thread_conflict",
                        )
                        continue
                    self._thread_to_session[child_thread_id] = session
            if self._child_threads.is_child(thread_id):
                session.emit_child_translated(item)
            else:
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
        """追加无法路由的诊断并写 debug 日志，不向任何会话发事件。"""

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
        """进入 degraded，关闭本代审批，并将本地在途 turn 标记为 host_exited。"""

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
        """向当前已注册会话广播进程状态；之后注册的会话不会补收。"""

        for session in self._sessions.values():
            session.emit_host_status(status, reason=reason)

    @staticmethod
    def _default_client_factory() -> AppServerClient:
        """返回未启动的默认 client；handler 注册与启动由 ``ensure_ready`` 完成。"""

        return AppServerClient()

    def _thread_start_params(self, session: CodexSession) -> dict[str, Any]:
        """保留会话配置中的非空覆盖项，并合并启用的 Trowel MCP 服务。"""

        return manager_params.thread_start_params(session)

    def _thread_resume_params(self, session: CodexSession) -> dict[str, Any]:
        """为已有 binding 重发工作目录、权限覆盖和不持久化的 MCP 配置。"""

        return manager_params.thread_resume_params(session)

    @staticmethod
    def _turn_start_params(
        thread_id: str,
        text: str,
        *,
        model: str | None = None,
        effort: str | None = None,
        approval: str | None = None,
        sandbox: str | None = None,
    ) -> dict[str, Any]:
        """构造文本输入，并只发送本轮显式提供的模型、推理强度和权限覆盖。"""

        return manager_params.turn_start_params(
            thread_id,
            text,
            model=model,
            effort=effort,
            approval=approval,
            sandbox=sandbox,
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
    """读取通知顶层的非空轮次 ID。"""

    value = params.get("turnId")
    return value if isinstance(value, str) and value else None


def _extract_turn_id(turn_result: Mapping[str, Any]) -> str:
    """从启动响应提取已创建的 ``turn.id``；缺失即表示协议漂移。"""

    turn = turn_result.get("turn")
    if not isinstance(turn, Mapping) or not turn.get("id"):
        raise ProtocolViolationError(
            "turn/start response has no turn.id",
            payload=dict(turn_result),
        )
    return str(turn["id"])
